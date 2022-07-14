from abc import ABC, abstractmethod
from typing import List, Union

import pystac

from pctasks.core.models.task import WaitTaskResult
from pctasks.core.storage import StorageFactory
from pctasks.dataset.chunks.task import CreateChunksTask
from pctasks.dataset.items.task import CreateItemsTask
from pctasks.dataset.splits.task import CreateSplitsTask
from pctasks.task import Task


class Collection(ABC):
    @classmethod
    @abstractmethod
    def create_item(
        cls, asset_uri: str, storage_factory: StorageFactory
    ) -> Union[List[pystac.Item], WaitTaskResult]:
        pass

    @classmethod
    def create_items_task(cls) -> Task:
        return CreateItemsTask(cls.create_item)

    @classmethod
    def create_splits_task(cls) -> Task:
        return CreateSplitsTask()

    @classmethod
    def create_chunks_task(cls) -> Task:
        return CreateChunksTask()


class PremadeItemCollection(Collection):
    @classmethod
    def create_item(
        cls, asset_uri: str, storage_factory: StorageFactory
    ) -> Union[List[pystac.Item], WaitTaskResult]:
        """
        Create items from URLs to GeoJSON files.

        Use this :ref:`Collection` subclass when your STAC items already exist
        as JSON files in some storage location. This reads the file
        """
        asset_storage, path = storage_factory.get_storage_for_file(asset_uri)
        item_href = asset_storage.get_authenticated_url(path)
        return [pystac.Item.from_file(item_href)]
