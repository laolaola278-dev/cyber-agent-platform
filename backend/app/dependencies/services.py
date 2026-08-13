"""FastAPI dependency factories for application services."""

from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.assessment import (
    AssessmentPlanner,
    AssessmentRegistry,
    AssessmentRuntime,
    AssessmentService,
    FakeAssessmentPlugin,
    FindingKnowledgeMapper,
    ResultNormalizer,
    RuleBasedRiskEngine,
)
from app.assets import AssetService
from app.capabilities.service import CapabilityRegistryService
from app.config import ConfigurationProvider
from app.database import get_db_session
from app.detection import (
    DetectionPlanner,
    DetectionRegistry,
    DetectionResultNormalizer,
    DetectionRuntime,
    DetectionService,
    FakeDetectionPlugin,
    RuleBasedCorrelationEngine,
)
from app.events import EventPublisher, EventType, InMemoryEventBus
from app.events.audit import AuditSubscriber
from app.evidence import EvidenceService
from app.incident import IncidentPlanner, IncidentRegistry, IncidentRuntime, IncidentService
from app.knowledge import (
    JSONKnowledgeImporter,
    KnowledgeImporter,
    KnowledgeRegistry,
    KnowledgeResolver,
)
from app.knowledge.service import KnowledgeService
from app.notification import (
    FakeNotificationPlugin,
    NotificationPlanner,
    NotificationPolicyEngine,
    NotificationRegistry,
    NotificationRuntime,
    NotificationService,
    RoutingEngine,
    TemplateProvider,
    default_templates,
)
from app.orchestrator import FirstAvailableStrategy, TaskDispatcher
from app.playbook import (
    PlaybookExecutor,
    PlaybookPlanner,
    PlaybookPolicy,
    PlaybookRegistry,
    PlaybookRuntime,
    PlaybookService,
)
from app.plugins.edr import EDRResponsePlugin
from app.plugins.firewall import FirewallResponsePlugin
from app.plugins.nuclei import NucleiAssessmentPlugin
from app.plugins.suricata import SuricataDetectionPlugin
from app.plugins.waf import WAFResponsePlugin
from app.plugins.zap import ZapAssessmentPlugin
from app.plugins.zeek import ZeekDetectionPlugin
from app.plugins.zeek.telemetry import ZeekTelemetryPlugin
from app.report import ReportService
from app.repositories import (
    AgentRepository,
    AssessmentPluginRepository,
    AssessmentReportRepository,
    AssessmentTaskRepository,
    AssetRepository,
    AuditRepository,
    CapabilityRepository,
    DetectionPluginRepository,
    DetectionTaskRepository,
    FindingRepository,
    IncidentRepository,
    InvestigationCaseRepository,
    KnowledgeRepository,
    KnowledgeSourceRepository,
    NotificationPlanRepository,
    NotificationPluginRepository,
    NotificationTemplateRepository,
    PlaybookExecutionRepository,
    PlaybookRepository,
    PlaybookTriggerRepository,
    PlaybookVersionRepository,
    ResponsePlanRepository,
    ResponsePluginRepository,
    ResponsePolicyRepository,
    SecurityEventRepository,
    SQLAlchemyCheckpointProvider,
    TaskRepository,
    TelemetryRepository,
    TicketRepository,
    ToolRepository,
    WorkflowDefinitionRepository,
    WorkflowInstanceRepository,
)
from app.response import (
    ApprovalService,
    FakeResponsePlugin,
    ResponsePlanner,
    ResponsePolicyEngine,
    ResponseRegistry,
    ResponseRuntime,
    ResponseService,
    RollbackService,
)
from app.runtime.manager import RuntimeManager
from app.runtime.service import RuntimeService
from app.runtime.services import ServiceProvider
from app.sandbox import (
    LocalProcessSandbox,
    MemorySecretProvider,
    SandboxPolicyEngine,
    SandboxProfile,
    SandboxRuntime,
    SecretReference,
)
from app.schemas.assessment import ASSESSMENT_CAPABILITIES
from app.schemas.detection import DETECTION_CAPABILITIES
from app.schemas.notification import NOTIFICATION_CAPABILITIES
from app.schemas.response import RESPONSE_CAPABILITIES
from app.services.audit import AuditService
from app.services.registry import AgentRegistryService, ToolRegistryService
from app.services.task import TaskService
from app.telemetry import (
    FakeTelemetryPlugin,
    MemoryCheckpointProvider,
    MemoryTelemetryJournal,
    TelemetryPlanner,
    TelemetryRegistry,
    TelemetryRuntime,
)
from app.telemetry.service import TelemetryService
from app.tool_manager import ToolFactory, ToolManager
from app.tools.edr import EDRAdapter, EDRPolicyProvider, MockEDRProvider
from app.tools.firewall import (
    FirewallAdapter,
    FirewallPolicyProvider,
    MockFirewallProvider,
)
from app.tools.nuclei import ApprovedNucleiTemplate, NucleiAdapter
from app.tools.suricata import SuricataAdapter, SuricataDataSource, SuricataSandboxProfile
from app.tools.waf import MockWAFProvider, WAFAdapter, WAFPolicyProvider
from app.tools.zap import ZapAdapter, ZapSandboxProfile, ZapV2ApiClient
from app.tools.zeek import ZeekAdapter, ZeekDataSource, ZeekSandboxProfile
from app.worker import (
    PluginWorkerRuntime,
    WorkerHeartbeat,
    WorkerLeaseManager,
    WorkerManager,
    WorkerRecord,
    WorkerRegistry,
    WorkerRuntime,
    WorkerScheduler,
    WorkerStatus,
)
from app.workflow import WorkflowRuntime, WorkflowService
from app.zeek import ZeekTelemetryBridge

SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]


def get_configuration(request: Request) -> ConfigurationProvider:
    """Return the application-scoped, strongly typed configuration provider."""

    return request.app.state.configuration_provider


ConfigurationDependency = Annotated[ConfigurationProvider, Depends(get_configuration)]


def _worker_capabilities() -> frozenset[str]:
    return frozenset(
        ASSESSMENT_CAPABILITIES
        | DETECTION_CAPABILITIES
        | RESPONSE_CAPABILITIES
        | NOTIFICATION_CAPABILITIES
        | {"telemetry.receive", "telemetry.transform", "telemetry.publish"}
    )


async def get_worker_runtime(
    request: Request,
    session: SessionDependency,
) -> WorkerRuntime:
    registry = WorkerRegistry(session)
    worker = await registry.register(
        WorkerRecord(
            name="memory-worker",
            runtime_version="phase-18.1",
            capabilities=_worker_capabilities(),
            max_concurrency=1024,
        )
    )
    if worker.status is WorkerStatus.REGISTERED:
        await registry.heartbeat(
            WorkerHeartbeat(
                worker_id=worker.id,
                status=WorkerStatus.ONLINE,
                active_executions=0,
            ),
            actor="startup",
        )
    leases = WorkerLeaseManager(session)
    sandbox = SandboxRuntime(request.app.state.sandbox_provider, SandboxPolicyEngine())
    return WorkerRuntime(
        session,
        registry,
        WorkerScheduler(registry),
        leases,
        sandbox,
    )


async def get_worker_manager(
    runtime: Annotated[WorkerRuntime, Depends(get_worker_runtime)],
    session: SessionDependency,
) -> WorkerManager:
    registry = runtime.registry
    return WorkerManager(registry, WorkerLeaseManager(session), runtime)


def get_secret_provider(
    request: Request,
    session: SessionDependency,
) -> MemorySecretProvider:
    return request.app.state.secret_provider.with_session(session)


async def get_zap_api_key(
    provider: Annotated[MemorySecretProvider, Depends(get_secret_provider)],
    configuration: ConfigurationDependency,
) -> str:
    reference = SecretReference(
        name=configuration.assessment.zap.api_key_secret_reference,
        purpose="OWASP ZAP API authentication",
    )
    resolved = await provider.resolve(reference)
    return resolved.value.get_secret_value()


ZapApiKeyDependency = Annotated[str, Depends(get_zap_api_key)]


async def get_plugin_worker_runtime(
    runtime: Annotated[WorkerRuntime, Depends(get_worker_runtime)],
) -> PluginWorkerRuntime:
    return PluginWorkerRuntime(runtime, SandboxProfile(name="default-plugin-sandbox"))


WorkerManagerDependency = Annotated[WorkerManager, Depends(get_worker_manager)]
WorkerRuntimeDependency = Annotated[WorkerRuntime, Depends(get_worker_runtime)]
PluginWorkerRuntimeDependency = Annotated[PluginWorkerRuntime, Depends(get_plugin_worker_runtime)]


def get_event_publisher(session: SessionDependency) -> EventPublisher:
    """Create one request-scoped event bus with transactional audit consumption."""

    bus = InMemoryEventBus()
    AuditSubscriber(AuditService(session, AuditRepository(session))).register(bus)
    return bus


EventPublisherDependency = Annotated[EventPublisher, Depends(get_event_publisher)]


def get_asset_service(
    session: SessionDependency,
    publisher: EventPublisherDependency,
) -> AssetService:
    return AssetService(session, AssetRepository(session), publisher)


AssetServiceDependency = Annotated[AssetService, Depends(get_asset_service)]


def get_knowledge_service(
    session: SessionDependency,
    publisher: EventPublisherDependency,
) -> KnowledgeService:
    registry = KnowledgeRegistry()
    registry.register_importer(JSONKnowledgeImporter())
    repository = KnowledgeRepository(session)
    importer = KnowledgeImporter(
        session,
        repository,
        KnowledgeSourceRepository(session),
        publisher,
        registry,
        KnowledgeResolver(registry),
    )
    return KnowledgeService(session, repository, importer, publisher)


KnowledgeServiceDependency = Annotated[KnowledgeService, Depends(get_knowledge_service)]


def get_assessment_service(
    session: SessionDependency,
    configuration: ConfigurationDependency,
    publisher: EventPublisherDependency,
    worker_runtime: PluginWorkerRuntimeDependency,
    zap_api_key: ZapApiKeyDependency,
) -> AssessmentService:
    registry = AssessmentRegistry()
    registry.register(FakeAssessmentPlugin())
    nuclei_config = configuration.assessment.nuclei
    template_root = (configuration.config_directory / nuclei_config.template_root).resolve()
    approved_templates = {
        template_id: ApprovedNucleiTemplate(
            template_id=template_id,
            path=(template_root / item.path).resolve(),
            sha256=item.sha256,
            max_requests=item.max_requests,
        )
        for template_id, item in nuclei_config.approved_templates.items()
    }
    registry.register(
        NucleiAssessmentPlugin(
            NucleiAdapter(
                LocalProcessSandbox({nuclei_config.executable}),
                executable=nuclei_config.executable,
                template_root=template_root,
                approved_templates=approved_templates,
                max_output_bytes=nuclei_config.max_output_bytes,
            )
        )
    )
    zap_config = configuration.assessment.zap
    zap_profile = ZapSandboxProfile(
        cpu_limit=zap_config.sandbox.cpu_limit,
        memory_limit_mb=zap_config.sandbox.memory_limit_mb,
        timeout_seconds=zap_config.sandbox.timeout_seconds,
        network_policy=zap_config.sandbox.network_policy,
    )
    zap_adapter = ZapAdapter(
        ZapV2ApiClient(
            api_url=zap_config.api_url,
            api_key=zap_api_key,
        ),
        LocalProcessSandbox(set()),
        profile=zap_profile,
        allowed_scan_policies=frozenset(zap_config.allowed_scan_policies),
    )
    registry.register(ZapAssessmentPlugin(zap_adapter))
    planner = AssessmentPlanner(registry)
    knowledge_repository = KnowledgeRepository(session)
    return AssessmentService(
        session,
        AssessmentTaskRepository(session),
        FindingRepository(session),
        AssessmentPluginRepository(session),
        AssessmentReportRepository(session),
        CapabilityRegistryService(session, CapabilityRepository(session)),
        registry,
        planner,
        AssessmentRuntime(registry, worker_runtime),
        ResultNormalizer(RuleBasedRiskEngine()),
        FindingKnowledgeMapper(knowledge_repository),
        publisher,
        configuration.assessment.policy,
        zap_adapter,
    )


AssessmentServiceDependency = Annotated[AssessmentService, Depends(get_assessment_service)]


def get_suricata_adapter(configuration: ConfigurationDependency) -> SuricataAdapter:
    config = configuration.detection.suricata
    sources = {
        source_id: SuricataDataSource(
            source_id=source_id.strip().casefold(),
            path=(configuration.config_directory / item.path).resolve(),
            fixture=item.fixture,
        )
        for source_id, item in config.data_sources.items()
    }
    sandbox = config.sandbox
    profile = SuricataSandboxProfile(
        cpu_limit=sandbox.cpu_limit,
        memory_limit_mb=sandbox.memory_limit_mb,
        timeout_seconds=sandbox.timeout_seconds,
        max_input_bytes=sandbox.max_input_bytes,
        max_records=sandbox.max_records,
        allowed_event_types=frozenset(item.casefold() for item in sandbox.allowed_event_types),
        filesystem_policy=sandbox.filesystem_policy,
        network_policy=sandbox.network_policy,
    )
    return SuricataAdapter(sources, profile=profile)


SuricataAdapterDependency = Annotated[SuricataAdapter, Depends(get_suricata_adapter)]


def get_zeek_adapter(configuration: ConfigurationDependency) -> ZeekAdapter:
    config = configuration.detection.zeek
    sources = {
        source_id: ZeekDataSource(
            source_id=source_id.strip().casefold(),
            path=(configuration.config_directory / item.path).resolve(),
            fixture=item.fixture,
        )
        for source_id, item in config.data_sources.items()
    }
    sandbox = config.sandbox
    profile = ZeekSandboxProfile(
        cpu_limit=sandbox.cpu_limit,
        memory_limit_mb=sandbox.memory_limit_mb,
        timeout_seconds=sandbox.timeout_seconds,
        max_input_bytes=sandbox.max_input_bytes,
        max_records=sandbox.max_records,
        allowed_logs=frozenset(item.casefold() for item in sandbox.allowed_logs),
        filesystem_policy=sandbox.filesystem_policy,
        network_policy=sandbox.network_policy,
    )
    return ZeekAdapter(sources, profile=profile)


ZeekAdapterDependency = Annotated[ZeekAdapter, Depends(get_zeek_adapter)]


def get_detection_service(
    session: SessionDependency,
    configuration: ConfigurationDependency,
    publisher: EventPublisherDependency,
    suricata_adapter: SuricataAdapterDependency,
    worker_runtime: PluginWorkerRuntimeDependency,
) -> DetectionService:
    registry = DetectionRegistry()
    registry.register(FakeDetectionPlugin())
    registry.register(SuricataDetectionPlugin(suricata_adapter))
    registry.register(ZeekDetectionPlugin())
    planner = DetectionPlanner(registry)
    return DetectionService(
        session,
        DetectionTaskRepository(session),
        SecurityEventRepository(session),
        DetectionPluginRepository(session),
        CapabilityRegistryService(session, CapabilityRepository(session)),
        registry,
        planner,
        DetectionRuntime(registry, worker_runtime),
        DetectionResultNormalizer(),
        RuleBasedCorrelationEngine(),
        publisher,
        configuration.detection.policy,
    )


DetectionServiceDependency = Annotated[DetectionService, Depends(get_detection_service)]


def get_incident_service(
    session: SessionDependency,
    configuration: ConfigurationDependency,
    publisher: EventPublisherDependency,
) -> IncidentService:
    registry = IncidentRegistry()
    planner = IncidentPlanner(registry)
    return IncidentService(
        session,
        IncidentRepository(session),
        InvestigationCaseRepository(session),
        registry,
        planner,
        IncidentRuntime(),
        publisher,
        configuration.incident.policy,
    )


IncidentServiceDependency = Annotated[IncidentService, Depends(get_incident_service)]


def get_mock_waf_provider(request: Request) -> MockWAFProvider:
    """Keep synthetic WAF state stable within one application instance."""

    provider = getattr(request.app.state, "mock_waf_provider", None)
    if provider is None:
        provider = MockWAFProvider()
        request.app.state.mock_waf_provider = provider
    return provider


WAFProviderDependency = Annotated[MockWAFProvider, Depends(get_mock_waf_provider)]


def get_mock_firewall_provider(request: Request) -> MockFirewallProvider:
    """Keep synthetic Firewall state stable within one application instance."""

    provider = getattr(request.app.state, "mock_firewall_provider", None)
    if provider is None:
        provider = MockFirewallProvider()
        request.app.state.mock_firewall_provider = provider
    return provider


FirewallProviderDependency = Annotated[MockFirewallProvider, Depends(get_mock_firewall_provider)]


def get_mock_edr_provider(request: Request) -> MockEDRProvider:
    """Keep synthetic EDR observed state stable within one application instance."""

    provider = getattr(request.app.state, "mock_edr_provider", None)
    if provider is None:
        provider = MockEDRProvider()
        request.app.state.mock_edr_provider = provider
    return provider


EDRProviderDependency = Annotated[MockEDRProvider, Depends(get_mock_edr_provider)]


def get_response_service(
    session: SessionDependency,
    configuration: ConfigurationDependency,
    publisher: EventPublisherDependency,
    waf_provider: WAFProviderDependency,
    firewall_provider: FirewallProviderDependency,
    edr_provider: EDRProviderDependency,
    worker_runtime: PluginWorkerRuntimeDependency,
) -> ResponseService:
    registry = ResponseRegistry()
    registry.register(FakeResponsePlugin())
    registry.register(
        WAFResponsePlugin(
            WAFAdapter(
                waf_provider,
                WAFPolicyProvider(),
            )
        )
    )
    registry.register(
        FirewallResponsePlugin(
            FirewallAdapter(
                firewall_provider,
                FirewallPolicyProvider(),
            )
        )
    )
    registry.register(
        EDRResponsePlugin(
            EDRAdapter(
                edr_provider,
                EDRPolicyProvider(),
            )
        )
    )
    policy_engine = ResponsePolicyEngine()
    return ResponseService(
        session,
        ResponsePlanRepository(session),
        ResponsePluginRepository(session),
        ResponsePolicyRepository(session),
        CapabilityRegistryService(session, CapabilityRepository(session)),
        registry,
        ResponsePlanner(registry, policy_engine),
        ResponseRuntime(registry, worker_runtime),
        ApprovalService(),
        RollbackService(),
        publisher,
        configuration.response.policy,
    )


ResponseServiceDependency = Annotated[ResponseService, Depends(get_response_service)]


def get_notification_service(
    session: SessionDependency,
    configuration: ConfigurationDependency,
    publisher: EventPublisherDependency,
    worker_runtime: PluginWorkerRuntimeDependency,
) -> NotificationService:
    registry = NotificationRegistry()
    registry.register(FakeNotificationPlugin())
    templates = TemplateProvider()
    for template in default_templates():
        templates.register(template)
    policy_engine = NotificationPolicyEngine()
    routing = RoutingEngine()
    planner = NotificationPlanner(registry, policy_engine, routing, templates)
    return NotificationService(
        session,
        NotificationPlanRepository(session),
        NotificationPluginRepository(session),
        NotificationTemplateRepository(session),
        TicketRepository(session),
        CapabilityRegistryService(session, CapabilityRepository(session)),
        registry,
        templates,
        planner,
        NotificationRuntime(registry, worker_runtime),
        publisher,
        configuration.notification.policy,
    )


NotificationServiceDependency = Annotated[NotificationService, Depends(get_notification_service)]


@lru_cache(maxsize=1)
def get_telemetry_memory_state() -> tuple[MemoryCheckpointProvider, MemoryTelemetryJournal]:
    return MemoryCheckpointProvider(), MemoryTelemetryJournal()


def get_telemetry_service(
    session: SessionDependency,
    configuration: ConfigurationDependency,
    publisher: EventPublisherDependency,
    worker_runtime: PluginWorkerRuntimeDependency,
) -> TelemetryService:
    registry = TelemetryRegistry()
    registry.register(FakeTelemetryPlugin())
    zeek_adapter = get_zeek_adapter(configuration)
    registry.register(ZeekTelemetryPlugin(zeek_adapter))
    planner = TelemetryPlanner(registry)
    config = configuration.telemetry.telemetry
    from app.schemas.telemetry import TelemetryPolicy

    policy = TelemetryPolicy(
        allowed_plugins=config.allowed_plugins,
        allowed_streams=config.allowed_streams,
        timeout_seconds=config.timeout_seconds,
        max_records=config.max_records,
        max_record_size_bytes=config.max_record_size_bytes,
        batch_size=config.batch_size,
        window_seconds=config.window_seconds,
        queue_capacity=config.queue_capacity,
        backpressure_action=config.backpressure_action,
        retry_attempts=config.retry_attempts,
        pause_seconds=config.pause_seconds,
    )
    memory_checkpoints, journal = get_telemetry_memory_state()
    checkpoint_provider = (
        SQLAlchemyCheckpointProvider(session)
        if config.checkpoint_provider == "database"
        else memory_checkpoints
    )
    return TelemetryService(
        session,
        TelemetryRepository(session),
        planner,
        TelemetryRuntime(registry, worker_runtime),
        publisher,
        policy,
        checkpoint_provider,
        journal,
    )


TelemetryServiceDependency = Annotated[TelemetryService, Depends(get_telemetry_service)]


def get_zeek_telemetry_bridge(
    configuration: ConfigurationDependency,
    zeek_adapter: ZeekAdapterDependency,
    worker_runtime: PluginWorkerRuntimeDependency,
) -> ZeekTelemetryBridge:
    registry = TelemetryRegistry()
    registry.register(ZeekTelemetryPlugin(zeek_adapter))
    planner = TelemetryPlanner(registry)
    config = configuration.telemetry.telemetry
    from app.schemas.telemetry import TelemetryPolicy

    policy = TelemetryPolicy(
        allowed_plugins=config.allowed_plugins,
        allowed_streams=config.allowed_streams,
        timeout_seconds=config.timeout_seconds,
        max_records=config.max_records,
        max_record_size_bytes=config.max_record_size_bytes,
        batch_size=config.batch_size,
        window_seconds=config.window_seconds,
        queue_capacity=config.queue_capacity,
        backpressure_action=config.backpressure_action,
        retry_attempts=config.retry_attempts,
        pause_seconds=config.pause_seconds,
    )
    return ZeekTelemetryBridge(
        zeek_adapter,
        planner,
        TelemetryRuntime(registry, worker_runtime),
        policy,
    )


ZeekTelemetryBridgeDependency = Annotated[ZeekTelemetryBridge, Depends(get_zeek_telemetry_bridge)]


async def get_runtime_service(
    session: SessionDependency,
    configuration: ConfigurationDependency,
    publisher: EventPublisherDependency,
) -> AsyncIterator[RuntimeService]:
    """Construct and reliably dispose the request-scoped Runtime stack."""

    evidence_path = (
        configuration.config_directory / configuration.runtime.runtime.evidence_directory
    ).resolve()
    evidence = EvidenceService(session, publisher, evidence_path)
    report = ReportService(session, publisher)
    tool_manager = ToolManager(
        ToolRepository(session), ToolFactory.with_platform_defaults(), publisher
    )
    services = ServiceProvider()
    services.register(EvidenceService, evidence)
    services.register(ReportService, report)
    services.register(ToolManager, tool_manager)
    manager = RuntimeManager(
        session,
        publisher,
        services,
        report,
        configuration.runtime.runtime.model_dump(mode="python"),
    )
    registry = AgentRegistryService(
        session, AgentRepository(session), publisher, configuration.registry
    )
    tool_registry = ToolRegistryService(
        session, ToolRepository(session), publisher, configuration.registry
    )
    runtime_service = RuntimeService(
        session,
        configuration,
        manager,
        registry,
        tool_registry,
        tool_manager,
    )
    try:
        yield runtime_service
    finally:
        await tool_manager.shutdown_all()


RuntimeServiceDependency = Annotated[RuntimeService, Depends(get_runtime_service)]


def get_agent_service(
    session: SessionDependency,
    configuration: ConfigurationDependency,
    publisher: EventPublisherDependency,
) -> AgentRegistryService:
    return AgentRegistryService(
        session, AgentRepository(session), publisher, configuration.registry
    )


def get_tool_service(
    session: SessionDependency,
    configuration: ConfigurationDependency,
    publisher: EventPublisherDependency,
) -> ToolRegistryService:
    return ToolRegistryService(session, ToolRepository(session), publisher, configuration.registry)


def get_task_service(
    session: SessionDependency,
    configuration: ConfigurationDependency,
    publisher: EventPublisherDependency,
    runtime_service: RuntimeServiceDependency,
) -> TaskService:
    if configuration.orchestrator.dispatcher.scheduling_strategy != "first_available":
        raise ValueError("Unsupported scheduling strategy")
    repository = TaskRepository(session)
    dispatcher = TaskDispatcher(
        session,
        repository,
        AgentRepository(session),
        publisher,
        FirstAvailableStrategy(),
        configuration.orchestrator,
        configuration.registry,
        runtime_service,
        CapabilityRepository(session),
    )
    return TaskService(session, repository, publisher, dispatcher, runtime_service)


TaskServiceDependency = Annotated[TaskService, Depends(get_task_service)]


def get_workflow_service(
    session: SessionDependency,
    publisher: EventPublisherDependency,
    task_service: TaskServiceDependency,
) -> WorkflowService:
    instance_repository = WorkflowInstanceRepository(session)
    runtime = WorkflowRuntime(
        session,
        instance_repository,
        publisher,
        task_service,
    )
    return WorkflowService(
        session,
        WorkflowDefinitionRepository(session),
        instance_repository,
        publisher,
        runtime,
    )


AgentServiceDependency = Annotated[AgentRegistryService, Depends(get_agent_service)]
ToolServiceDependency = Annotated[ToolRegistryService, Depends(get_tool_service)]
WorkflowServiceDependency = Annotated[WorkflowService, Depends(get_workflow_service)]


def get_playbook_service(
    session: SessionDependency,
    publisher: EventPublisherDependency,
    assessment: AssessmentServiceDependency,
    detection: DetectionServiceDependency,
    response: ResponseServiceDependency,
    notification: NotificationServiceDependency,
) -> PlaybookService:
    policy = PlaybookPolicy(
        allowed_capabilities=frozenset(
            ASSESSMENT_CAPABILITIES
            | DETECTION_CAPABILITIES
            | RESPONSE_CAPABILITIES
            | NOTIFICATION_CAPABILITIES
        )
    )
    executor = PlaybookExecutor(assessment, detection, response, notification)
    runtime = PlaybookRuntime(
        session,
        PlaybookExecutionRepository(session),
        executor,
        publisher,
        policy,
    )
    service = PlaybookService(
        session,
        PlaybookRepository(session),
        PlaybookVersionRepository(session),
        PlaybookExecutionRepository(session),
        PlaybookTriggerRepository(session),
        PlaybookRegistry(PlaybookVersionRepository(session)),
        PlaybookPlanner(policy),
        runtime,
        policy,
        publisher,
    )
    publisher.subscribe(EventType.INCIDENT_CREATED, service.handle_incident_created)
    return service


PlaybookServiceDependency = Annotated[PlaybookService, Depends(get_playbook_service)]
