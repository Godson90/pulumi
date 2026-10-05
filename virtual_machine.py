"""Standalone Azure test VM as a Pulumi ComponentResource.

Creates: resource group, NSG, public IP (optional), NIC, Linux VM.
A random admin password is generated per VM instance.

Azure cannot attach a VM without a subnet, so until the shared VNet exists
this component builds a minimal throwaway VNet. Pass subnet_id to skip it.
"""

import base64
from dataclasses import dataclass
from typing import Dict, List, Optional

import pulumi
import pulumi_random as random
from pulumi_azure_native import compute, network, resources

# Temporary: only used when no subnet_id is supplied. Remove with the VNet phase.
_TEST_VNET_CIDR = "10.250.0.0/24"


@dataclass(kw_only=True)
class AzureVmArgs:
    vm_name: str
    admin_username: str
    vm_size: str
    os_image: str  # Publisher:Offer:Sku:Version
    service_port: int
    allowed_source_prefixes: List[str]
    enable_public_ip: bool
    tags: Dict[str, str]
    subnet_id: Optional[pulumi.Input[str]] = None  # None -> throwaway test VNet


class AzureVm(pulumi.ComponentResource):
    def __init__(self, name: str, args: AzureVmArgs, opts: Optional[pulumi.ResourceOptions] = None):
        super().__init__("defenstack:azure:TestVm", name, None, opts)
        self._name = name
        self._args = args
        self._child = pulumi.ResourceOptions(parent=self)

        self.resource_group = resources.ResourceGroup(f"{name}-rg", tags=args.tags, opts=self._child)
        self.admin_password = self._generate_password()
        subnet_id = args.subnet_id if args.subnet_id is not None else self._test_subnet()
        nsg = self._nsg()
        self.public_ip = self._public_ip() if args.enable_public_ip else None
        nic = self._nic(subnet_id, nsg)
        self.vm = self._vm(nic)

        self.ip = self.public_ip.ip_address if self.public_ip else None
        self.fqdn = (
            self.public_ip.dns_settings.apply(lambda d: d.fqdn if d else None) if self.public_ip else None
        )
        self.url = (
            self.fqdn.apply(lambda h: f"http://{h}:{args.service_port}" if h else None) if self.fqdn else None
        )

        self.register_outputs({
            "resourceGroup": self.resource_group.name,
            "vmId": self.vm.id,
            "publicIp": self.ip,
            "fqdn": self.fqdn,
            "url": self.url,
        })

    # -- building blocks ----------------------------------------------------

    def _generate_password(self) -> pulumi.Output[str]:
        # Unique per instance; meets Azure rules (12-123 chars, 3 of 4 classes).
        return random.RandomPassword(
            f"{self._name}-admin-password",
            length=24,
            special=True,
            override_special="!#%*-_=+?",
            min_upper=1,
            min_lower=1,
            min_numeric=1,
            min_special=1,
            opts=self._child,
        ).result

    def _test_subnet(self) -> pulumi.Output[str]:
        vnet = network.VirtualNetwork(
            f"{self._name}-test-vnet",
            resource_group_name=self.resource_group.name,
            address_space=network.AddressSpaceArgs(address_prefixes=[_TEST_VNET_CIDR]),
            subnets=[network.SubnetArgs(name="default", address_prefix=_TEST_VNET_CIDR)],
            tags=self._args.tags,
            opts=self._child,
        )
        return vnet.subnets.apply(lambda s: s[0].id)

    def _nsg(self) -> network.NetworkSecurityGroup:
        a = self._args
        rules = [
            network.SecurityRuleArgs(
                name=rule_name,
                priority=priority,
                direction="Inbound",
                access="Allow",
                protocol="Tcp",
                source_port_range="*",
                destination_port_range=port,
                source_address_prefixes=a.allowed_source_prefixes,
                destination_address_prefix="*",
            )
            for priority, (rule_name, port) in enumerate(
                [("allow-ssh", "22"), ("allow-service", str(a.service_port))], start=1000
            )
        ] if a.allowed_source_prefixes else []

        return network.NetworkSecurityGroup(
            f"{self._name}-nsg",
            resource_group_name=self.resource_group.name,
            security_rules=rules,
            tags=a.tags,
            opts=self._child,
        )

    def _public_ip(self) -> network.PublicIPAddress:
        return network.PublicIPAddress(
            f"{self._name}-pip",
            resource_group_name=self.resource_group.name,
            sku=network.PublicIPAddressSkuArgs(name="Standard"),
            public_ip_allocation_method="Static",
            dns_settings=network.PublicIPAddressDnsSettingsArgs(domain_name_label=self._args.vm_name),
            tags=self._args.tags,
            opts=self._child,
        )

    def _nic(self, subnet_id,
             nsg: network.NetworkSecurityGroup) -> network.NetworkInterface:
        return network.NetworkInterface(
            f"{self._name}-nic",
            resource_group_name=self.resource_group.name,
            network_security_group=network.NetworkSecurityGroupArgs(id=nsg.id),
            ip_configurations=[network.NetworkInterfaceIPConfigurationArgs(
                name="ipconfig1",
                subnet=network.SubnetArgs(id=subnet_id),
                private_ip_allocation_method="Dynamic",
                public_ip_address=network.PublicIPAddressArgs(id=self.public_ip.id) if self.public_ip else None,
            )],
            tags=self._args.tags,
            opts=self._child,
        )

    def _init_script(self) -> str:
        # Placeholder test workload: HTTP server on service_port.
        return f"""#!/bin/bash
set -euo pipefail
mkdir -p /srv/www
echo "Hello from $(hostname)" > /srv/www/index.html
cat > /etc/systemd/system/demo-http.service <<'UNIT'
[Unit]
Description=Demo HTTP server
After=network-online.target

[Service]
WorkingDirectory=/srv/www
ExecStart=/usr/bin/python3 -m http.server {self._args.service_port}
Restart=always
DynamicUser=yes
AmbientCapabilities=CAP_NET_BIND_SERVICE

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now demo-http.service
"""

    def _vm(self, nic: network.NetworkInterface) -> compute.VirtualMachine:
        a = self._args
        parts = a.os_image.split(":")
        if len(parts) != 4:
            raise ValueError(f"os_image must be Publisher:Offer:Sku:Version, got '{a.os_image}'")
        publisher, offer, sku, version = parts

        return compute.VirtualMachine(
            f"{self._name}-vm",
            resource_group_name=self.resource_group.name,
            hardware_profile=compute.HardwareProfileArgs(vm_size=a.vm_size),
            identity=compute.VirtualMachineIdentityArgs(type=compute.ResourceIdentityType.SYSTEM_ASSIGNED),
            network_profile=compute.NetworkProfileArgs(
                network_interfaces=[compute.NetworkInterfaceReferenceArgs(id=nic.id, primary=True)]
            ),
            os_profile=compute.OSProfileArgs(
                computer_name=a.vm_name,
                admin_username=a.admin_username,
                admin_password=self.admin_password,
                custom_data=base64.b64encode(self._init_script().encode()).decode(),
                linux_configuration=compute.LinuxConfigurationArgs(
                    disable_password_authentication=False,
                    patch_settings=compute.LinuxPatchSettingsArgs(
                        patch_mode="AutomaticByPlatform",
                        assessment_mode="AutomaticByPlatform",
                    ),
                ),
            ),
            security_profile=compute.SecurityProfileArgs(
                security_type="TrustedLaunch",
                uefi_settings=compute.UefiSettingsArgs(secure_boot_enabled=True, v_tpm_enabled=True),
            ),
            storage_profile=compute.StorageProfileArgs(
                image_reference=compute.ImageReferenceArgs(
                    publisher=publisher, offer=offer, sku=sku, version=version
                ),
                os_disk=compute.OSDiskArgs(
                    create_option="FromImage",
                    delete_option="Delete",
                    managed_disk=compute.ManagedDiskParametersArgs(storage_account_type="StandardSSD_LRS"),
                ),
            ),
            diagnostics_profile=compute.DiagnosticsProfileArgs(
                boot_diagnostics=compute.BootDiagnosticsArgs(enabled=True)
            ),
            tags=a.tags,
            opts=self._child,
        )