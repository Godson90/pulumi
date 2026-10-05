import pulumi

# Import from the new component module:
from website import AzureStaticWebsite

# import vm module
from virtual_machine import AzureVm, AzureVmArgs

config = pulumi.Config()
vm = AzureVm(
    "vm",
    AzureVmArgs(
        vm_name=config.require("vmName"),
        admin_username=config.require("adminUsername"),
        vm_size=config.require("vmSize"),
        os_image=config.require("osImage"),
        service_port=config.require_int("servicePort"),
        allowed_source_prefixes=config.require_object("allowedSourcePrefixes"),
        enable_public_ip=config.require_bool("enablePublicIp"),
        tags={"project": pulumi.get_project(), "stack": pulumi.get_stack()},
    ),
)

pulumi.export("resourceGroup", vm.resource_group.name)
pulumi.export("adminUsername", config.require("adminUsername"))
pulumi.export("adminPassword", vm.admin_password)  # secret; --show-secrets to view
pulumi.export("publicIp", vm.ip)
pulumi.export("fqdn", vm.fqdn)
pulumi.export("url", vm.url)

# Create an instance of the component with the same files as before:
website = AzureStaticWebsite('my-website', files=['static/index.html'])

# And export its autoassigned URL:
pulumi.export("url", website.url)