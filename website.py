import pulumi
from pulumi_azure_native import storage, resources
from typing import List

# A component that encapsulates creating an Azure hosted static website.
class AzureStaticWebsite(pulumi.ComponentResource):
    def __init__(self, name: str, files: List[str] = None, opts = None):
        super().__init__('quickstart:index:AzureStaticWebsite', name, { 'files': files }, opts)
        # Create an Azure Resource Group
        resource_group = resources.ResourceGroup("resource_group")
        opts = pulumi.ResourceOptions(parent=self)

        # Create an Azure Storage Account
        storage_account = storage.StorageAccount(
            "sa",
            resource_group_name=resource_group.name,
            sku={
                "name": storage.SkuName.STANDARD_LRS,
            },
            kind=storage.Kind.STORAGE_V2,
            # Permit containers/blobs to be configured for anonymous (public) read access
            allow_blob_public_access=True,
            # Reachable from the public internet...
            public_network_access=storage.PublicNetworkAccess.ENABLED,
            # ...with the firewall defaulting to allow (no IP/VNet restrictions)
            network_rule_set={
                "default_action": storage.DefaultAction.ALLOW,
                "bypass": storage.Bypass.AZURE_SERVICES,
            },
            opts=opts,
        )

        # Enable static website support
        static_website = storage.StorageAccountStaticWebsite(
            "static_website",
            account_name=storage_account.name,
            resource_group_name=resource_group.name,
            index_document="index.html",
            opts=opts,
        )
        # Upload the file
        index_html = storage.Blob(
            "index_html",
            resource_group_name=resource_group.name,
            account_name=storage_account.name,
            content_type="text/html",
            container_name=static_website.container_name,
            source=pulumi.FileAsset("./static/index.html"),
            opts=opts,
        )

        # A container with anonymous blob-level read access (individual blobs are
        # publicly readable, but the container itself cannot be listed)
        public_container = storage.BlobContainer(
            "stackblob",
            account_name=storage_account.name,
            resource_group_name=resource_group.name,
            public_access=storage.PublicAccess.BLOB,
            opts=opts,
        )

        # The same file(s) served directly from the public blob endpoint
        for file in (files or []):
            storage.Blob(
                file.replace("/", "_"),
                resource_group_name=resource_group.name,
                account_name=storage_account.name,
                content_type="text/html",
                container_name=public_container.name,
                source=pulumi.FileAsset(file),
                opts=opts,
            )

        # Capture the URL and make it available as a component property and output:
        self.url = storage_account.primary_endpoints.apply(lambda pe: pe.web)
        self.register_outputs({'url': self.url})  # Signal component completion.