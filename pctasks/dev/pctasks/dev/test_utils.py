import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from click.testing import CliRunner, Result

from pctasks.cli.cli import pctasks_cmd
from pctasks.client.client import PCTasksClient
from pctasks.client.errors import NotFoundError
from pctasks.client.settings import ClientSettings
from pctasks.client.submit.template import template_workflow_dict
from pctasks.core.constants import (
    DEFAULT_LOG_CONTAINER,
    DEFAULT_TASK_IO_CONTAINER,
    DEFAULT_TASK_RUN_RECORD_TABLE_NAME,
)
from pctasks.core.models.api import JobRunResponse, TaskRunResponse, WorkflowRunResponse
from pctasks.core.models.base import RunRecordId
from pctasks.core.models.record import (
    JobRunStatus,
    TaskRunRecord,
    TaskRunStatus,
    WorkflowRunStatus,
)
from pctasks.core.models.task import (
    CompletedTaskResult,
    TaskRunConfig,
    TaskRunMessage,
    WaitTaskResult,
)
from pctasks.core.models.tokens import StorageAccountTokens
from pctasks.core.models.workflow import WorkflowConfig, WorkflowSubmitMessage
from pctasks.core.storage.blob import BlobStorage
from pctasks.dev.config import get_blob_config, get_table_config
from pctasks.dev.env import (
    PCTASKS_BLOB_ACCOUNT_KEY_ENV_VAR,
    PCTASKS_BLOB_ACCOUNT_NAME_ENV_VAR,
    PCTASKS_BLOB_ACCOUNT_URL_ENV_VAR,
    get_dev_env,
)

from pctasks.run.utils import get_run_log_path
from pctasks.task.run import run_task


class CliTestError(Exception):
    pass


def run_pctasks(
    cmd: List[Any], catch_exceptions: bool = False, silent: bool = False
) -> Result:
    runner = CliRunner(mix_stderr=False)

    if len(cmd) == 0:
        raise Exception("Empty command")

    result = runner.invoke(pctasks_cmd, [str(c) for c in cmd], catch_exceptions=True)
    if result.output and not silent:
        print(result.output)
    if result.exception is not None and not catch_exceptions:
        raise CliTestError("Test code threw an exception") from result.exception
    return result


@dataclass
class TestJobRunRecords:
    job_record: JobRunResponse
    tasks: Dict[str, TaskRunResponse]


@dataclass
class TestWorkflowRunRecords:
    workflow_record: WorkflowRunResponse
    jobs: Dict[str, TestJobRunRecords]
    timeout: bool = False


def wait_for_test_workflow_run(
    run_id: str, timeout_seconds: int = 10
) -> TestWorkflowRunRecords:
    print(f"Waiting for test workflow run {run_id} to complete...")
    workflow: Optional[WorkflowRunResponse] = None

    client = PCTasksClient()

    tic = time.perf_counter()
    tok = time.perf_counter()
    while (
        workflow is None
        or workflow.status
        not in [WorkflowRunStatus.COMPLETED, WorkflowRunStatus.FAILED]
    ) and tok - tic < timeout_seconds:
        if not workflow:
            print(f"waiting for workflow run record... ({tok - tic:.0f} s)".format(tok))
        else:
            print(
                f"Retrying workflow run with status {workflow.status}... "
                f"({tok - tic:.0f} s)".format(tok)
            )
        try:
            workflow = client.get_workflow(run_id)
        except NotFoundError:
            pass

        time.sleep(1)
        tok = time.perf_counter()

    if workflow:
        jobs = client.get_jobs_from_workflow(workflow)

        job_records: Dict[str, TestJobRunRecords] = {}
        for job in jobs:
            tasks = client.get_tasks_from_job(job)
            job_records[job.job_id] = TestJobRunRecords(
                job, {t.task_id: t for t in tasks}
            )

        return TestWorkflowRunRecords(
            workflow, job_records, timeout=tok - tic >= timeout_seconds
        )
    else:
        raise Exception(f"Timeout while waiting for workflow {run_id}")


def _check_workflow(
    run_id: str, timeout_seconds: int = 10
) -> Tuple[bool, TestWorkflowRunRecords]:
    workflow_run_records = wait_for_test_workflow_run(run_id, timeout_seconds)
    failed = workflow_run_records.workflow_record.status != WorkflowRunStatus.COMPLETED
    if failed:
        print(
            f"Workflow run {run_id} failed. "
            f"Status: {workflow_run_records.workflow_record.status}"
        )
        if workflow_run_records.timeout:
            print(f"TIMEOUT while waiting for workflow {run_id}")
        if workflow_run_records.workflow_record.workflow:
            print("Workflow:")
            print(workflow_run_records.workflow_record.workflow.to_yaml())
        for error in workflow_run_records.workflow_record.errors or []:
            print(f" - {error}")
    for job_id, job_records in workflow_run_records.jobs.items():
        job_failed = job_records.job_record.status != JobRunStatus.COMPLETED
        if job_failed:
            print(f"Job {job_id} failed. Status: {job_records.job_record.status}")
            for error in job_records.job_record.errors or []:
                print(f" -- {error}")
        failed |= job_failed
        for task_id, task_record in job_records.tasks.items():
            task_failed = task_record.status != TaskRunStatus.COMPLETED
            if task_failed:
                print(f"Task {task_id} failed")
                for error in task_record.errors or []:
                    print(f" --- {error}")
            failed |= task_failed
            if task_failed:
                run_log_path = get_run_log_path(
                    job_id=job_id, task_id=task_id, run_id=run_id
                )

                log_storage = BlobStorage.from_account_key(
                    f"blob://{get_dev_env(PCTASKS_BLOB_ACCOUNT_NAME_ENV_VAR)}"
                    f"/{DEFAULT_LOG_CONTAINER}",
                    account_key=get_dev_env(PCTASKS_BLOB_ACCOUNT_KEY_ENV_VAR),
                    account_url=get_dev_env(PCTASKS_BLOB_ACCOUNT_URL_ENV_VAR),
                )

                if log_storage.file_exists(run_log_path):
                    print(" -- Run log: --")
                    print(log_storage.read_text(run_log_path))
                    print(" -- End run log: --")
                else:
                    print(f"No run log found at {run_log_path}")

    return (failed, workflow_run_records)


def assert_workflow_is_successful(
    run_id: str, timeout_seconds: int = 10
) -> TestWorkflowRunRecords:
    failed, records = _check_workflow(run_id, timeout_seconds)
    assert not failed
    return records


def assert_workflow_fails(
    run_id: str, timeout_seconds: int = 10
) -> TestWorkflowRunRecords:
    failed, records = _check_workflow(run_id, timeout_seconds)
    assert failed
    return records


def run_workflow(
    workflow: Union[str, WorkflowConfig],
    args: Optional[Dict[str, Any]] = None,
    base_path: Union[str, Path] = Path.cwd(),
) -> str:
    """Runs a workflow from either a YAML string or WorkflowConfig object.
    Uses the default submit settings.
    Returns the run_id
    """
    workflow = (
        WorkflowConfig.from_yaml(workflow) if isinstance(workflow, str) else workflow
    )
    templated_workflow = template_workflow_dict(workflow.dict(), base_path=base_path)
    submit_settings = ClientSettings.get()
    submit_message = PCTasksClient(submit_settings).submit_workflow(
        WorkflowSubmitMessage(workflow=templated_workflow, args=args)
    )
    return submit_message.run_id


def run_workflow_from_file(
    workflow_path: Union[str, Path],
    args: Optional[Dict[str, Any]] = None,
) -> str:
    """Runs a workflow from a YAML file at workflow_path.
    Uses the default submit settings.
    Returns the run_id
    """
    return run_workflow(
        Path(workflow_path).read_text(),
        args=args,
        base_path=Path(workflow_path).parent,
    )


def run_test_task(
    args: Dict[str, Any],
    task: str,
    tokens: Optional[Dict[str, StorageAccountTokens]] = None,
) -> Union[CompletedTaskResult, WaitTaskResult]:
    from pctasks.dev.tables import get_task_run_record_table

    job_id = "unit-test-job"
    task_id = "task-unit-test"
    run_id = "test_task_func"

    run_record_id = RunRecordId(
        job_id=job_id,
        task_id=task_id,
        run_id=run_id,
    )

    with get_task_run_record_table() as task_run_table:
        task_run_table.upsert_record(
            TaskRunRecord(
                run_id=run_record_id.run_id,
                job_id=job_id,
                task_id=task_id,
                status=TaskRunStatus.SUBMITTED,
            )
        )

        log_path = f"{job_id}/{task_id}/{run_id}.log"
        output_path = f"{job_id}/{task_id}/{run_id}-output.json"

        msg = TaskRunMessage(
            args=args,
            config=TaskRunConfig(
                run_id=run_id,
                job_id=job_id,
                task_id=task_id,
                image="TESTIMAGE:latest",
                tokens=tokens,
                task=task,
                task_runs_table_config=get_table_config(
                    DEFAULT_TASK_RUN_RECORD_TABLE_NAME
                ),
                log_blob_config=get_blob_config(DEFAULT_LOG_CONTAINER, log_path),
                output_blob_config=get_blob_config(
                    DEFAULT_TASK_IO_CONTAINER, output_path
                ),
            ),
        )

        result = run_task(msg)
        if isinstance(result, CompletedTaskResult):
            record = task_run_table.get_record(
                run_record_id=run_record_id,
            )
            assert record
            assert record.status == TaskRunStatus.COMPLETED

            return result
        else:
            assert isinstance(result, WaitTaskResult)
            return result
