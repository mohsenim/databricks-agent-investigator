"""Failure classes and the priors from termination codes."""
from enum import Enum


class FailureClass(str, Enum):
    CODE_OR_CONFIG = "code_or_config_error"
    SCHEMA_CHANGE = "upstream_schema_change"
    DATA_PROBLEM = "upstream_data_problem"
    UPSTREAM_JOB = "upstream_job_failure"
    CLUSTER_INFRA = "cluster_or_infrastructure_error"
    PERMISSION = "permission_change"
    LIBRARY = "library_install_failure"
    RESOURCE_LIMIT = "resource_limit"
    TRANSIENT_UNKNOWN = "transient_or_unknown"


class Confidence(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class Status(str, Enum):
    RUNNING = "running"
    DONE = "done"
    INCONCLUSIVE = "inconclusive"
    ERROR = "error"


# Databricks termination codes (Jobs API and system tables) mapped to the
# class they point to. This is only a starting point: the evidence decides.
TERMINATION_PRIORS: dict[str, FailureClass] = {
    "LIBRARY_INSTALLATION_ERROR": FailureClass.LIBRARY,
    "UNAUTHORIZED_ERROR": FailureClass.PERMISSION,
    "STORAGE_ACCESS_ERROR": FailureClass.PERMISSION,
    "CLUSTER_ERROR": FailureClass.CLUSTER_INFRA,
    "INVALID_CLUSTER_REQUEST": FailureClass.CLUSTER_INFRA,
    "CLUSTER_REQUEST_LIMIT_EXCEEDED": FailureClass.CLUSTER_INFRA,
    "CLOUD_FAILURE": FailureClass.CLUSTER_INFRA,
    "DRIVER_ERROR": FailureClass.CLUSTER_INFRA,
    "MAX_SPARK_CONTEXTS_EXCEEDED": FailureClass.CLUSTER_INFRA,
    "RESOURCE_NOT_FOUND": FailureClass.CODE_OR_CONFIG,
    "INVALID_RUN_CONFIGURATION": FailureClass.CODE_OR_CONFIG,
    "REPOSITORY_CHECKOUT_FAILED": FailureClass.CODE_OR_CONFIG,
    "MAX_CONCURRENT_RUNS_EXCEEDED": FailureClass.TRANSIENT_UNKNOWN,
    "MAX_JOB_QUEUE_SIZE_EXCEEDED": FailureClass.TRANSIENT_UNKNOWN,
    "WORKSPACE_RUN_LIMIT_EXCEEDED": FailureClass.TRANSIENT_UNKNOWN,
}

# RUN_EXECUTION_ERROR and FEATURE_DISABLED have no prior: the error text decides.


def prior_for(result_state: str | None, termination_code: str | None) -> FailureClass | None:
    if result_state == "TIMED_OUT":
        return FailureClass.RESOURCE_LIMIT
    if termination_code:
        return TERMINATION_PRIORS.get(termination_code)
    return None
