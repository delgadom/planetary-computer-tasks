import datetime
import json
import logging

import azure.storage.queue
import pystac

from pctasks.core.constants import (
    AZURITE_HOST_ENV_VAR,
    AZURITE_PORT_ENV_VAR,
    AZURITE_STORAGE_ACCOUNT_ENV_VAR,
)
from pctasks.core.storage import StorageFactory
from pctasks.dataset import streaming
from pctasks.dev.blob import temp_azurite_blob_storage
from pctasks.dev.constants import AZURITE_ACCOUNT_KEY
from pctasks.dev.queues import TempQueue
from pctasks.task.context import TaskContext
from pctasks.task.streaming import StreamingTaskOptions

BLANK_ITEM = pystac.Item(
    "id",
    geometry={},
    bbox=None,
    datetime=datetime.datetime(2000, 1, 1),
    properties={},
)


class CreateItems:
    def __init__(self):
        self.count = 0

    def __call__(self, asset_uri, storage_factory):
        self.count += 1
        result = BLANK_ITEM.full_copy()
        return [result]


# XXX: These should probably mock Cosmos, so that we don't trigger change feed.


def test_process_message():
    task = streaming.StreamingCreateItemsTask()
    create_items = CreateItems()
    message = azure.storage.queue.QueueMessage(
        content=json.dumps(
            {
                "data": {"url": "test.tif"},
                "time": "2023-03-27T21:12:27.7409548Z",
            }
        )
    )
    context = TaskContext(run_id="test", storage_factory=StorageFactory())
    task_input = streaming.StreamingCreateItemsInput(
        collection_id="test",
        create_items_function=create_items,
        streaming_options=StreamingTaskOptions(
            # process_message doesn't actually touch the queue
            queue_url="http://example.com",
            queue_credential=AZURITE_ACCOUNT_KEY,
            visibility_timeout=10,
            message_limit=5,
        ),
    )
    items_containers = task.get_extra_options(task_input, context)["items_containers"]

    task.process_message(
        message,
        task_input,
        context,
        items_containers,
        create_items_function=create_items,
    )


def test_streaming_create_items_task():
    # This implicitly uses
    # - azurite for queues, ...
    task = streaming.StreamingCreateItemsTask()
    create_items = CreateItems()

    with TempQueue(
        message_decode_policy=None, message_encode_policy=None
    ) as queue_client:
        # put some messages on the queue
        for _ in range(10):
            queue_client.send_message(
                json.dumps(
                    {
                        "data": {"url": "test.tif"},
                        "time": "2023-03-27T21:12:27.7409548Z",
                    }
                )
            )
        task_input = streaming.StreamingCreateItemsInput(
            collection_id="test",
            create_items_function=create_items,
            streaming_options=StreamingTaskOptions(
                queue_url=queue_client.url,
                queue_credential=AZURITE_ACCOUNT_KEY,
                visibility_timeout=10,
                message_limit=5,
            ),
        )
        context = TaskContext(run_id="test", storage_factory=StorageFactory())

        task.run(task_input, context)
        assert create_items.count == 10
        # Hmm is this zero because all the items were created successfully?
        # We need to ensure that process_message is unit tested
        assert queue_client.get_queue_properties().approximate_message_count == 0


def test_streaming_create_items_from_message():
    task = streaming.StreamingCreateItemsTask()

    class MyCreateItems:
        def __init__(self):
            self.items = []

        def __call__(self, asset_uri, storage_factory):
            asset_uri = json.loads(asset_uri)  # done by pctasks base class
            items = streaming.create_item_from_message(asset_uri, storage_factory)
            self.items.extend(items)
            return items

    create_items = MyCreateItems()
    item = pystac.Item(
        "id", {}, None, datetime.datetime(2000, 1, 1), {}, collection="test"
    )

    with TempQueue(
        message_decode_policy=None, message_encode_policy=None
    ) as queue_client:
        # put some messages on the queue
        for _ in range(10):
            queue_client.send_message(
                json.dumps(
                    {
                        "data": {"url": json.dumps(item.to_dict())},
                        "time": "2023-03-27T21:12:27.7409548Z",
                    }
                )
            )

        task_input = streaming.StreamingCreateItemsInput(
            collection_id="test",
            create_items_function=create_items,
            streaming_options=StreamingTaskOptions(
                queue_url=queue_client.url,
                queue_credential=AZURITE_ACCOUNT_KEY,
                visibility_timeout=10,
                message_limit=5,
            ),
        )
        context = TaskContext(run_id="test", storage_factory=StorageFactory())

        task.run(task_input, context)
    assert create_items.items[0].to_dict() == item.to_dict()


def test_streaming_create_items_rewrite_url(monkeypatch):
    """
    Test ensuring that the streaming create items task can read from blob storage.

    This also verifies that the URL rewriting from https:// URLs to blob:// works.
    """
    monkeypatch.setenv(AZURITE_STORAGE_ACCOUNT_ENV_VAR, "devstoreaccount1")
    monkeypatch.setenv(AZURITE_HOST_ENV_VAR, "localhost")
    monkeypatch.setenv(AZURITE_PORT_ENV_VAR, "10000")

    # TODO: temp_storage stuff. actually read the file.

    with temp_azurite_blob_storage() as root_storage:
        root_storage.write_bytes(
            "data/item.json", json.dumps(BLANK_ITEM.to_dict()).encode()
        )

        url = root_storage.get_url("data/item.json")
        assert url.startswith("http://localhost:10000")

        task = streaming.StreamingCreateItemsTask()

        def create_items(asset_uri, storage_factory):
            assert asset_uri == root_storage.get_uri("data/item.json")
            result = streaming.create_item_from_item_uri(asset_uri, storage_factory)
            return result

        message_data = {"url": url}
        context = TaskContext(run_id="test", storage_factory=StorageFactory())

        result = task.create_items(
            message_data,
            create_items,
            context.storage_factory,
            collection_id="test-collection",
        )
        expected = BLANK_ITEM.full_copy()
        expected.collection_id = "test-collection"
        assert result[0].to_dict() == expected.to_dict()


def test_streaming_create_items_task_invalid_item(caplog):
    # This implicitly uses
    # - azurite for queues, ...
    task = streaming.StreamingCreateItemsTask()
    bad_item = BLANK_ITEM.clone()
    bad_item.datetime = None  # now invalid
    create_items = lambda *args, **kwargs: [bad_item]

    logger = logging.getLogger("pctasks.task.streaming")
    # logger.setLevel(logging.INFO)
    # handler = logging.StreamHandler()
    # handler.setLevel(logging.INFO)
    logger.addHandler(caplog.handler)

    with TempQueue(
        message_decode_policy=None, message_encode_policy=None
    ) as queue_client:
        # put some messages on the queue
        for _ in range(10):
            queue_client.send_message(
                json.dumps(
                    {
                        "data": {"url": "test.tif"},
                        "time": "2023-03-27T21:12:27.7409548Z",
                    }
                )
            )
        task_input = streaming.StreamingCreateItemsInput(
            collection_id="test",
            create_items_function=create_items,
            streaming_options=StreamingTaskOptions(
                queue_url=queue_client.url,
                queue_credential=AZURITE_ACCOUNT_KEY,
                visibility_timeout=1,
                message_limit=5,
            ),
        )
        context = TaskContext(run_id="test", storage_factory=StorageFactory())


        with caplog.at_level(logging.CRITICAL, logger="pctasks.task.streaming"):
            task.run(task_input, context)

    # assert caplog.records
    assert 0