from pctasks.core.cosmos.containers.workflow_runs import WorkflowRunsContainer
from pctasks.core.models.run import (
    JobPartitionRunRecord,
    JobPartitionRunStatus,
    JobRunRecord,
    JobRunStatus,
    RunRecordType,
    TaskRunRecord,
    TaskRunStatus,
    WorkflowRunRecord,
)
from pctasks.core.models.workflow import WorkflowRunStatus
from pctasks.dev.cosmosdb import temp_cosmosdb


def test_job_part_pagination():
    run_id = "test-run"
    job_id = "test-job"
    dataset_id = "test-dataset"
    with temp_cosmosdb() as db:
        workflow_run = WorkflowRunRecord(
            workflow_id="test-workflow",
            run_id=run_id,
            dataset_id=dataset_id,
            status=WorkflowRunStatus.RUNNING,
            jobs=[
                JobRunRecord(status=JobRunStatus.RUNNING, run_id=run_id, job_id=job_id)
            ],
        )

        workflow_runs = WorkflowRunsContainer(WorkflowRunRecord, db=db)
        workflow_runs.put(workflow_run)

        assert workflow_runs.get(run_id, partition_key=run_id)

        job_parts = [
            JobPartitionRunRecord(
                job_id=job_id,
                status=JobPartitionRunStatus.RUNNING,
                run_id=run_id,
                partition_id="0",
                tasks=[
                    TaskRunRecord(
                        status=TaskRunStatus.RUNNING,
                        run_id=run_id,
                        job_id=job_id,
                        partition_id="0",
                        task_id="test-task",
                    )
                ],
            ),
            JobPartitionRunRecord(
                job_id=job_id,
                status=JobPartitionRunStatus.PENDING,
                run_id=run_id,
                partition_id="1",
                tasks=[
                    TaskRunRecord(
                        status=TaskRunStatus.PENDING,
                        run_id=run_id,
                        job_id=job_id,
                        partition_id="1",
                        task_id="test-task",
                    )
                ],
            ),
        ]

        job_partition_runs = WorkflowRunsContainer(JobPartitionRunRecord, db=db)
        for task_group_run in job_parts:
            job_partition_runs.put(task_group_run)

        pages = list(
            job_partition_runs.query_paged(
                partition_key=run_id,
                query="SELECT * FROM c WHERE c.job_id = @job_id and c.type = @type",
                parameters={"@job_id": job_id, "type": RunRecordType.JOB_PARTITION_RUN},
                page_size=1,
            )
        )

        assert len(pages) == 2

        pages2 = list(
            job_partition_runs.query_paged(
                partition_key=run_id,
                query="SELECT * FROM c WHERE c.job_id = @job_id and c.type = @type",
                parameters={"@job_id": job_id, "type": RunRecordType.JOB_PARTITION_RUN},
                page_size=1,
                continuation_token=pages[0].continuation_token,
            )
        )

        assert len(pages2) == 1

        items1_2 = list(pages[1])
        items2_1 = list(pages2[0])

        assert len(items1_2) == 1
        assert len(items2_1) == 1

        assert items1_2[0].partition_id == items2_1[0].partition_id

        # Test job_run.job_partition_counts trigger update

        def fetch_job_run() -> JobRunRecord:
            fetched_workflow_run = workflow_runs.get(run_id, partition_key=run_id)
            assert fetched_workflow_run is not None
            assert len(fetched_workflow_run.jobs) == 1
            return fetched_workflow_run.jobs[0]

        job_run = fetch_job_run()
        assert job_run.job_partition_counts[JobPartitionRunStatus.PENDING] == 1
        assert job_run.job_partition_counts[JobPartitionRunStatus.RUNNING] == 1

        job_parts[1].set_status(JobPartitionRunStatus.RUNNING)
        job_partition_runs.put(job_parts[1])

        job_run = fetch_job_run()
        assert job_run.job_partition_counts[JobPartitionRunStatus.PENDING] == 0
        assert job_run.job_partition_counts[JobPartitionRunStatus.RUNNING] == 2
