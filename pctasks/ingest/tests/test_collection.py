import json
import pathlib
from typing import Any, Dict

import pystac
import strictyaml

from pctasks.core.models.workflow import WorkflowConfig
from pctasks.ingest.models import IngestTaskConfig, IngestTaskInput

HERE = pathlib.Path(__file__).parent
TEST_COLLECTION = HERE / "data-files/test_collection.json"
TEST_GOES_WORKFLOW = HERE / "data-files/goes-collection-workflow.yaml"


def test_collection_ser() -> None:
    with open(TEST_COLLECTION, "r") as f:
        collection = json.load(f)

    task = IngestTaskConfig.from_collection(collection=collection, target="staging")

    input = IngestTaskInput.parse_obj(task.args)
    ser_collection = input.content
    assert collection == ser_collection


def test_goes_coll_deser() -> None:
    with open(TEST_GOES_WORKFLOW, "r") as f:
        workflow = WorkflowConfig.from_yaml(f.read())

    task = workflow.jobs["ingest-collection"].tasks[0]
    input = IngestTaskInput.parse_obj(task.args)
    assert isinstance(input.content, dict)
    collection_dict: Dict[str, Any] = input.content
    print(json.dumps(collection_dict["extent"], indent=2))
    collection = pystac.Collection.from_dict(collection_dict)
    collection.validate()
