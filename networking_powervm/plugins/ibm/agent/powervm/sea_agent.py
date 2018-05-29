# Copyright 2014, 2017 IBM Corp.
#
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import sys

import eventlet
from neutron.common import config as n_config
from neutron.conf.agent import common as a_config
from oslo_config import cfg
from oslo_log import log as logging
from pypowervm.tasks import network_bridger as net_br
from pypowervm.wrappers import logical_partition as pvm_lpar
from pypowervm.wrappers import network as pvm_net

from networking_powervm.plugins.ibm.agent.powervm import agent_base
from networking_powervm.plugins.ibm.agent.powervm import constants as p_const
from networking_powervm.plugins.ibm.agent.powervm import prov_req as preq
from networking_powervm.plugins.ibm.agent.powervm import utils


eventlet.monkey_patch()

LOG = logging.getLogger(__name__)


agent_opts = [
    cfg.StrOpt('bridge_mappings',
               default='',
               help='The Network Bridge mappings (defined by the SEA) that '
                    'describe how the neutron physical networks map to the '
                    'Shared Ethernet Adapters.'
                    'Format: <ph_net1>:<sea1>:<vio1>,<ph_net2>:<sea2>:<vio2> '
                    'Example: default:ent5:vios_1,speedy:ent6:vios_1'),
    cfg.BoolOpt('automated_powervm_vlan_cleanup', default=True,
                help='Determines whether or not the VLANs will be removed '
                     'from the Network Bridge if a VM is removed and it is '
                     'the last VM on the system to use that VLAN.  By '
                     'default, will clean up VLANs to improve the overall '
                     'system performance (by reducing broadcast domain).  '
                     'Will only apply to VLANs not on the primary PowerVM '
                     'virtual Ethernet adapter of the SEA.')
]


cfg.CONF.register_opts(agent_opts, "AGENT")
a_config.register_agent_state_opts_helper(cfg.CONF)
a_config.register_root_helper(cfg.CONF)

ACONF = cfg.CONF.AGENT

VIF_TYPE_PVM_SEA = 'pvm_sea'


class SharedEthernetNeutronAgent(agent_base.BasePVMNeutronAgent):
    """Provides VLAN networks for Shared Ethernet Adapters on VIOSes.

    Designed to be compatible with the ML2 Neutron Plugin.
    """

    @property
    def agent_id(self):
        return 'sea-agent-%s' % cfg.CONF.host

    @property
    def agent_binary_name(self):
        """Name of the executable under which the SEA agent runs."""
        return p_const.AGENT_BIN_SEA

    @property
    def agent_type(self):
        return p_const.AGENT_TYPE_PVM_SEA

    @property
    def vif_wrapper_class(self):
        return pvm_net.CNA

    @property
    def vif_type(self):
        return VIF_TYPE_PVM_SEA

    def parse_bridge_mappings(self):
        return utils.parse_sea_mappings(self.adapter, self.host_uuid,
                                        ACONF.bridge_mappings)

    def heal_and_optimize(self):
        """Heals the system's network bridges and optimizes.

        Will query neutron for all the ports in use on this host.  Ensures that
        all of the VLANs needed for those ports are available on the correct
        network bridge.

        Finally, it optimizes the system by removing any VLANs that may no
        longer be required.  The VLANs that are removed must meet the following
        conditions:
         - Are not in use by ANY virtual machines on the system.  OpenStack
           managed or not.
         - Are not part of the primary load group on the Network Bridge.
        """
        LOG.info("Running the heal and optimize flow.")

        # Get a map of all the partitions and their CNAs.
        all_lpar_cnas = utils.list_vifs(self.adapter, self.vif_wrapper_class,
                                        include_vios_and_mgmt=True)
        # There can be CNAs (non-trunk) on VIOSes as well.  They should be
        # taken into account for the overall vifs.
        overall_vifs = [vif for vifs in all_lpar_cnas.values() for vif in vifs]
        # The lpar_cna_map includes only the client VMs.  Don't include mgmt.
        lpar_cna_map = {par_w: vifs for par_w, vifs in all_lpar_cnas.items()
                        if isinstance(par_w, pvm_lpar.LPAR) and
                        not par_w.is_mgmt_partition}

        # Build out all of the devices that we have available to us.  Some
        # may be on the system but not part of OpenStack.  Those get ignored.
        prov_reqs = preq.ProvisionRequest.for_wrappers(self, lpar_cna_map,
                                                       preq.PLUG)
        # Dictionary of the required VLANs on the Network Bridge
        nb_req_vlans = {}
        nb_wraps = utils.list_bridges(self.adapter, self.host_uuid)
        for nb_wrap in nb_wraps:
            nb_req_vlans[nb_wrap.uuid] = set()

        # Call down to the provision.  This will call device up on the
        # requests.
        self.provision_devices(prov_reqs)

        # Make sure that the provision requests VLAN is captured in the
        # nb_req_vlans list...so that the VLAN is not accidentally removed.
        for req in prov_reqs:
            nb_uuid, req_vlan = self._get_nb_and_vlan(req.rpc_device,
                                                      emit_warnings=False)
            nb_req_vlans[nb_uuid].add(req_vlan)

        # We should clean up old VLANs as well.  However, we only want to clean
        # up old VLANs that are not in use by ANYTHING in the system.
        #
        # The first step is to identify the VLANs that are needed.  That can
        # be done by extending our nb_req_vlans map.
        #
        # We first extend that map by listing all the VMs on the system
        # (whether managed by OpenStack or not) and then seeing what Network
        # Bridge uses them.
        vswitch_map = utils.get_vswitch_map(self.adapter, self.host_uuid)
        for client_adpt in overall_vifs:
            nb = utils.find_nb_for_cna(nb_wraps, client_adpt, vswitch_map)
            # Could occur if a system is internal only.
            if nb is None:
                LOG.debug("Client Adapter with mac %s is internal only.",
                          client_adpt.mac)
                continue

            # Make sure that it is on the nb_req_vlans list, as it is now
            # considered required.
            nb_req_vlans[nb.uuid].add(client_adpt.pvid)

            # Extend for each additional vlans as well
            for addl_vlan in client_adpt.tagged_vlans:
                nb_req_vlans[nb.uuid].add(addl_vlan)

        # The list of required VLANs on each network bridge also includes
        # everything on the primary VEA.
        for nb in nb_wraps:
            prim_ld_grp = nb.load_grps[0]
            vlans = [prim_ld_grp.pvid]
            vlans.extend(prim_ld_grp.tagged_vlans)
            for vlan in vlans:
                nb_req_vlans[nb.uuid].add(vlan)

        # If the configuration is set.
        if ACONF.automated_powervm_vlan_cleanup:
            self._cleanup_unused_vlans(nb_wraps, nb_req_vlans)

    def _cleanup_unused_vlans(self, nb_wraps, nb_req_vlans):
        cur_delete = 0

        # Loop through and remove VLANs that are no longer needed.
        for nb in nb_wraps:
            # Join the required vlans on the network bridge (already in
            # use) with the pending VLANs.
            req_vlans = nb_req_vlans[nb.uuid]

            # Get ALL the VLANs on the bridge
            existing_vlans = set(nb.list_vlans())

            # To determine the ones no longer needed, subtract from all the
            # VLANs the ones that are no longer needed.
            vlans_to_del = existing_vlans - req_vlans
            for vlan_to_del in vlans_to_del:
                if cur_delete < 3:
                    LOG.warning("Cleaning up VLAN %s from the system. It is "
                                "no longer in use.", vlan_to_del)
                    net_br.remove_vlan_from_nb(self.adapter, self.host_uuid,
                                               nb.uuid, vlan_to_del)
                else:
                    # We don't want to block on optimization for too long.
                    # Each VLAN clean up can take ~2 seconds, so if we do
                    # three of them, then that blocks deploys for about 6
                    # seconds.  We generally don't clean out VLANs that often
                    # but just in case, we get a rush of them, this ensures
                    # we don't block provision requests that are actually going
                    # on in the system.
                    LOG.warning(
                        "System identified that VLAN %s is unused. However, "
                        "three VLAN clean ups have already occurred in this "
                        "pass. Will clean up in next optimization pass.",
                        vlan_to_del)
                cur_delete += 1

    def provision_devices(self, requests):
        """Will ensure that the VLANs are on the NBs for the edge devices.

        Takes in a set of ProvisionRequests.  From those devices, determines
        the correct network bridges and their appropriate VLANs.  Then calls
        down to the pypowervm API to ensure that the required VLANs are
        on the appropriate ports.

        Will also ensure that the client side adapter is updated with the
        correct VLAN.

        :param requests: A list of ProvisionRequest objects.
        """
        # Only handle 'plug' requests.
        plug_reqs = {req for req in requests if req.action == preq.PLUG}
        nb_to_vlan = {}
        for p_req in plug_reqs:
            # Break the ports into their respective lists broken down by
            # Network Bridge.
            nb_uuid, vlan = self._get_nb_and_vlan(p_req.rpc_device,
                                                  emit_warnings=True)

            # A warning message will be printed to user if this were to occur
            if nb_uuid is None:
                continue

            if nb_to_vlan.get(nb_uuid) is None:
                nb_to_vlan[nb_uuid] = set()

            nb_to_vlan[nb_uuid].add(vlan)

        # For each bridge, make sure the VLANs are serviced.
        for nb_uuid in nb_to_vlan:
            net_br.ensure_vlans_on_nb(self.adapter, self.host_uuid, nb_uuid,
                                      nb_to_vlan.get(nb_uuid))

        # Now that the bridging is complete, let the superclass mark them as up
        super(SharedEthernetNeutronAgent, self).provision_devices(plug_reqs)
        LOG.debug('Successfully provisioned SEA VLANs for new devices.')

    def _get_nb_and_vlan(self, dev, emit_warnings=False):
        """Parses bridge mappings to find a match for the device passed in.

        :param dev: Neutron device to find a match for
        :param emit_warnings: (Optional) Defaults to False.  If true, will emit
                              a warning if the configuration is off.
        :return: UUID of the NetBridge
        :return: vlan for the neutron device
        """
        nb_uuid = self.br_map.get(dev.get('physical_network'))
        if not nb_uuid and emit_warnings:
            LOG.warning("Unable to determine the Network Bridge (Shared "
                        "Ethernet Adapter) for physical network %s.  Will be "
                        "unable to determine appropriate provisioning action.",
                        dev.get('physical_network'))
        return nb_uuid, dev.get('segmentation_id')


def main():
    # Read in the command line args
    n_config.init(sys.argv[1:])
    n_config.setup_logging()

    # Build then run the agent
    agent = SharedEthernetNeutronAgent()
    LOG.info("Shared Ethernet Agent initialized and running.")
    agent.rpc_loop()


if __name__ == "__main__":
    main()
