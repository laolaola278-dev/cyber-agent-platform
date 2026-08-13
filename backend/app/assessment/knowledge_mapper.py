"""Map scanner identifiers to existing version-pinned Knowledge Center records."""

from app.core.enums import KnowledgeType
from app.models import Knowledge, KnowledgeVersion
from app.repositories.knowledge import KnowledgeRepository
from app.schemas.assessment import AssessmentResult


class FindingKnowledgeMapper:
    def __init__(self, repository: KnowledgeRepository) -> None:
        self._repository = repository

    async def enrich(
        self, result: AssessmentResult
    ) -> dict[object, tuple[Knowledge, KnowledgeVersion]]:
        output: dict[object, tuple[Knowledge, KnowledgeVersion]] = {}
        for finding in result.findings:
            mapped_ids = list(finding.knowledge_ids)
            references = finding.attributes.get("knowledge_references", [])
            if isinstance(references, list):
                for reference in references:
                    if not isinstance(reference, dict):
                        continue
                    kind = str(reference.get("type", ""))
                    external_id = str(reference.get("external_id", ""))
                    if not kind or not external_id:
                        continue
                    candidates = [kind]
                    if kind == KnowledgeType.CVE.value:
                        candidates.append(KnowledgeType.CISA_KEV.value)
                    for candidate in candidates:
                        row = await self._repository.get_by_external_id(candidate, external_id)
                        if row is None:
                            continue
                        version = await self._repository.get_current_version(row)
                        if version is not None and row.id not in mapped_ids:
                            mapped_ids.append(row.id)
                            output[row.id] = (row, version)
            finding.knowledge_ids = mapped_ids
        return output
