"""Type-keyed service provider exposed through RuntimeContext."""

from typing import TypeVar, cast

ServiceT = TypeVar("ServiceT")


class ServiceProvider:
    """Resolve explicitly registered platform services by their public type."""

    def __init__(self) -> None:
        self._services: dict[type[object], object] = {}

    def register(self, service_type: type[ServiceT], service: ServiceT) -> None:
        if not isinstance(service, service_type):
            raise TypeError(f"Service must implement {service_type.__name__}")
        self._services[service_type] = service

    def resolve(self, service_type: type[ServiceT]) -> ServiceT:
        try:
            return cast(ServiceT, self._services[service_type])
        except KeyError as error:
            raise LookupError(f"Service {service_type.__name__} is not registered") from error
