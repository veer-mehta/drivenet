from abc import ABC, abstractmethod

VAULT_FILENAME = "drivenet-vault.enc"


class VaultStorage(ABC):
    @abstractmethod
    async def read(self, account):
        pass

    @abstractmethod
    async def write(self, account, blob):
        pass

    @abstractmethod
    async def delete(self, account):
        pass


class DriveTransport(ABC):
    @abstractmethod
    async def upload(self, account, name, data, meta=None):
        pass

    @abstractmethod
    async def download(self, account, drive_id):
        pass

    @abstractmethod
    async def delete(self, account, drive_id):
        pass

    @abstractmethod
    async def list_chunks(self, account):
        pass
