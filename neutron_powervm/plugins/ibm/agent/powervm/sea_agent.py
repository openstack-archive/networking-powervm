# Copyright 2014, 2015 IBM Corp.
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

import eventlet
eventlet.monkey_patch()

from oslo.config import cfg

from neutron.agent.common import config as a_config
from neutron.common import config as n_config
from neutron.i18n import _LW
from neutron.openstack.common import log as logging
from pypowervm.jobs import network_bridger as net_br

from neutron_powervm.plugins.ibm.agent.powervm import agent_base
from neutron_powervm.plugins.ibm.agent.powervm import constants as p_const

import sys


LOG = logging.getLogger(__name__)


agent_opts = [
    cfg.StrOpt('bridge_mappings',
               default='',
               help='The Network Bridge mappings (defined by the SEA) that '
                    'describes how the neutron physical networks map to the '
                    'Shared Ethernet Adapters.'
                    'Format: <ph_net1>:<sea1>:<vio1>,<ph_net2>:<sea2>:<vio2> '
                    'Example: default:ent5:vios_1,speedy:ent6:vios_1')
]


cfg.CONF.register_opts(agent_opts, "AGENT")
a_config.register_agent_state_opts_helper(cfg.CONF)
a_config.register_root_helper(cfg.CONF)

ACONF = cfg.CONF.AGENT


class SharedEthernetNeutronAgent(agent_base.BasePVMNeutronAgent):
    '''
    Provides VLAN networks for the PowerVM platform that run accross the
    Shared Ethernet within the Virtual I/O Servers.  Designed to be compatible
    with the ML2 Neutron Plugin.
    '''

    def __init__(self):
        """Constructs the agent."""
        name = 'neutron-powervm-sharedethernet-agent'
        agent_type = p_const.AGENT_TYPE_PVM_SEA
        super(SharedEthernetNeutronAgent, self).__init__(name, agent_type)

        self.br_map = self.api_utils.parse_sea_mappings(ACONF.bridge_mappings)

    def heal_and_optimize(self, is_boot):
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

        :param is_boot: Indicates if this is the first call on boot up of the
                        agent.
        """

        # List all our clients
        client_adpts = self.api_utils.list_client_adpts()

        # Get all the devices that Neutron knows for this host.  Note that
        # we pass in all of the macs on the system.  For VMs that neutron does
        # not know about, we get back an empty structure with just the mac.
        client_macs = [self.api_utils.norm_mac(x.mac) for x in client_adpts]
        devs = self.plugin_rpc.get_devices_details_list(self.context,
                                                        client_macs,
                                                        self.agent_id,
                                                        ACONF.pvm_host_mtms)

        # Dictionary of the required VLANs on the Network Bridge
        nb_req_vlans = {}
        nb_wraps = self.api_utils.list_bridges()
        for nb_wrap in nb_wraps:
            nb_req_vlans[nb_wrap.uuid] = set()

        for dev in devs:
            nb_uuid = self.br_map.get(dev.get('physical_network'))
            req_vlan = dev.get('segmentation_id')

            # This can happen for ports that are on the host, but not in
            # Neutron.
            if nb_uuid is None or req_vlan is None:
                continue

            # If that list does not contain my VLAN, add it
            nb_req_vlans[nb_uuid].add(req_vlan)

        # Lets ensure that all VLANs for the openstack VMs are on the network
        # bridges.
        for nb_uuid in nb_req_vlans.keys():
            net_br.ensure_vlans_on_nb(self.api_utils.adapter,
                                      self.api_utils.host_id, nb_uuid,
                                      nb_req_vlans[nb_uuid])

        # We should clean up old VLANs as well.  However, we only want to clean
        # up old VLANs that are not in use by ANYTHING in the system.
        #
        # The first step is to identify the VLANs that are needed.  That can
        # be done by extending our nb_req_vlans map.
        #
        # We first extend that map by listing all the VMs on the system
        # (whether managed by OpenStack or not) and then seeing what Network
        # Bridge uses them.
        vswitch_map = self.api_utils.get_vswitch_map()
        for client_adpt in client_adpts:
            nb = self.api_utils.find_nb_for_client_adpt(nb_wraps, client_adpt,
                                                        vswitch_map)
            # Could occur if a system is internal only.
            if nb is None:
                LOG.debug("Client Adapter with mac %s is internal only." %
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

        # At this point, all of the required vlans are captured in the
        # nb_req_vlans list.  We now subtract the VLANs on the Network Bridge
        # from the ones we identified as required.  That list are the
        # vlans to remove.
        for nb in nb_wraps:
            req_vlans = nb_req_vlans[nb.uuid]
            existing_vlans = set(nb.list_vlans())
            vlans_to_del = existing_vlans - req_vlans
            for vlan_to_del in vlans_to_del:
                LOG.warn(_LW("Cleaning up VLAN %(vlan)s from the system.  "
                             "It is no longer in use.") %
                         {'vlan': str(vlan_to_del)})
                net_br.remove_vlan_from_nb(self.api_utils.adapter,
                                           self.api_utils.host_id, nb.uuid,
                                           vlan_to_del)

    def provision_ports(self, ports):
        """Will ensure that the VLANs are on the NBs for the ports.

        Takes in a set of Neutron Ports.  From those ports, determines the
        correct network bridges and their appropriate VLANs.  Then calls
        down to the pypowervm API to ensure that the required VLANs are
        on the appropriate ports.

        :param ports: The new ports that are to be provisioned.  Is a set
                      of neutron ports.
        """
        dev_list = [x.get('mac_address') for x in ports]
        devs = self.plugin_rpc.get_devices_details_list(self.context,
                                                        dev_list,
                                                        self.agent_id,
                                                        ACONF.pvm_host_mtms)

        # Break the ports into their respective lists broken down by
        # Network Bridge.
        nb_to_vlan = {}
        for dev in devs:
            phys_net = dev.get('physical_network')
            nb_uuid = self.br_map.get(phys_net)
            if nb_to_vlan.get(nb_uuid) is None:
                nb_to_vlan[nb_uuid] = set()
            nb_to_vlan[nb_uuid].add(dev.get('segmentation_id'))

        # For each bridge, make sure the VLANs are serviced.
        for nb_uuid in nb_to_vlan.keys():
            net_br.ensure_vlans_on_nb(self.api_utils.adapter,
                                      self.api_utils.host_id, nb_uuid,
                                      nb_to_vlan.get(nb_uuid))


def main():
    # Read in the command line args
    n_config.init(sys.argv[1:])
    n_config.setup_logging()

    # Build then run the agent
    agent = SharedEthernetNeutronAgent()
    LOG.info(_("Shared Ethernet Agent initialized and running"))
    agent.rpc_loop()


if __name__ == "__main__":
    main()
