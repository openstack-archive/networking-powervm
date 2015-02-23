# Copyright 2014 IBM Corp.
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
#
# @author: Drew Thorstensen, IBM Corp.

import copy

import eventlet
eventlet.monkey_patch()

from oslo.config import cfg

from neutron.agent.common import config as a_config
from neutron.agent import rpc as agent_rpc
from neutron.common import config as n_config
from neutron.common import constants as q_const
from neutron.common import topics
from neutron import context as ctx
from neutron.i18n import _LW
from neutron.openstack.common import log as logging
from neutron.openstack.common import loopingcall
from pypowervm.jobs import network_bridger as net_br

from neutron_powervm.plugins.ibm.agent.powervm import constants as p_const
from neutron_powervm.plugins.ibm.agent.powervm import utils

import sys
import time


LOG = logging.getLogger(__name__)


agent_opts = [
    cfg.IntOpt('polling_interval', default=2,
               help=_("The number of seconds the agent will wait between "
                      "polling for local device changes.")),
    # TODO(thorst) Reevaluate as the API auth model evolves
    cfg.StrOpt('pvm_host_mtms',
               default='',
               help='The Model Type/Serial Number of the host server to '
                    'manage.  Format is MODEL-TYPE*SERIALNUM.  Example is '
                    '8286-42A*1234ABC.'),
    cfg.StrOpt('pvm_server_ip',
               default='localhost',
               help='The IP Address hosting the PowerVM REST API'),
    cfg.StrOpt('pvm_user_id',
               default='',
               help='The user id for authentication into the API.'),
    cfg.StrOpt('pvm_pass',
               default='',
               help='The password for authentication into the API.'),
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


class SharedEthernetPluginApi(agent_rpc.PluginApi):
    pass


class SharedEthernetRpcCallbacks(object):
    '''
    Provides call backs (as defined in the setup_rpc method within the
    SharedEthernetNeutronAgent class) that will be invoked upon certain
    actions from the controller.
    '''

    # This agent supports RPC Version 1.0.  For reference:
    #  1.0 Initial version
    #  1.1 Support Security Group RPC
    #  1.2 Support DVR (Distributed Virtual Router) RPC
    RPC_API_VERSION = '1.1'

    def __init__(self, agent):
        '''
        Creates the call back.  Most of the call back methods will be
        delegated to the agent.

        :param agent: The owning agent to delegate the callbacks to.
        '''
        super(SharedEthernetRpcCallbacks, self).__init__()
        self.agent = agent

    def port_update(self, context, **kwargs):
        port = kwargs['port']
        self.agent._update_port(port)
        LOG.debug(_("port_update RPC received for port: %s"), port['id'])

    def network_delete(self, context, **kwargs):
        network_id = kwargs.get('network_id')

        # TODO(thorst) Need to perform the call back
        LOG.debug(_("network_delete RPC received for network: %s"), network_id)


class SharedEthernetNeutronAgent():
    '''
    Provides VLAN networks for the PowerVM platform that run accross the
    Shared Ethernet within the Virtual I/O Servers.  Designed to be compatible
    with the ML2 Neutron Plugin.
    '''

    def __init__(self):
        '''
        Constructs the agent.
        '''
        # Define the baseline agent_state that will be reported back for the
        # health status
        self.agent_state = {
            'binary': 'neutron-powervm-sharedethernet-agent',
            'host': cfg.CONF.host,
            'topic': q_const.L2_AGENT_TOPIC,
            'configurations': {},
            'agent_type': p_const.AGENT_TYPE_PVM_SEA,
            'start_flag': True}
        self.setup_rpc()

        # A list of ports that maintains the list of current 'modified' ports
        self.updated_ports = set()

        # Create the utility class that enables work against the Hypervisors
        # Shared Ethernet NetworkBridge.
        password = ACONF.pvm_pass.decode('base64', 'strict')
        self.api_utils = utils.PVMUtils(ACONF.pvm_server_ip, ACONF.pvm_user_id,
                                        password, ACONF.pvm_host_mtms)

        self.br_map = self.api_utils.parse_sea_mappings(ACONF.bridge_mappings)

    def setup_rpc(self):
        '''
        Registers the RPC consumers for the plugin.
        '''
        self.agent_id = 'sea-agent-%s' % cfg.CONF.host
        self.topic = topics.AGENT
        self.plugin_rpc = SharedEthernetPluginApi(topics.PLUGIN)
        self.state_rpc = agent_rpc.PluginReportStateAPI(topics.PLUGIN)

        self.context = ctx.get_admin_context_without_session()

        # Defines what will be listening for incoming events from the
        # controller.
        self.endpoints = [SharedEthernetRpcCallbacks(self)]

        # Define the listening consumers for the agent.  ML2 only supports
        # these two update types.
        consumers = [[topics.PORT, topics.UPDATE],
                     [topics.NETWORK, topics.DELETE]]

        self.connection = agent_rpc.create_consumers(self.endpoints,
                                                     self.topic,
                                                     consumers)

        # Report interval is for the agent health check.
        report_interval = cfg.CONF.AGENT.report_interval
        if report_interval:
            hb = loopingcall.FixedIntervalLoopingCall(self._report_state)
            hb.start(interval=report_interval)

    def _report_state(self):
        '''
        Reports the state of the agent back to the controller.  Controller
        knows that if a response isn't provided in a certain period of time
        then the agent is dead.  This call simply tells the controller that
        the agent is alive.
        '''
        # TODO(thorst) provide some level of devices connected to this agent.
        try:
            device_count = 0
            self.agent_state.get('configurations')['devices'] = device_count
            self.state_rpc.report_state(self.context,
                                        self.agent_state)
            self.agent_state.pop('start_flag', None)
        except Exception:
            LOG.exception(_("Failed reporting state!"))

    def _update_port(self, port):
        '''
        Invoked to indicate that a port has been updated within Neutron.
        '''
        self.updated_ports.append(port)

    def _list_updated_ports(self):
        '''
        Will return (and then reset) the list of updated ports received
        from the system.
        '''
        ports = copy.copy(self.updated_ports)
        self.updated_ports = []
        return ports

    def _heal_and_optimize(self):
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
            for vlan in nb_req_vlans[nb_uuid]:
                # TODO(thorst) optimize
                net_br.ensure_vlan_on_nb(self.api_utils.adapter,
                                         self.api_utils.host_id, nb_uuid, vlan)

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

    def rpc_loop(self):
        '''
        Runs a check periodically to determine if new ports were added or
        removed.  Will call down to appropriate methods to determine correct
        course of action.
        '''

        loop_count = 0
        loop_reset_interval = 100

        while True:
            # If a new loop, heal and then iterate
            if loop_count == 0:
                LOG.debug("Performing heal and optimization of system.")
                self._heal_and_optimize()

            # Increment the loop
            if loop_count == loop_reset_interval:
                loop_count = 0
            else:
                loop_count += 1

            # Determine if there are new ports
            u_ports = self._list_updated_ports()

            # If there are no updated ports, just sleep and re-loop
            if not u_ports:
                LOG.debug("No changes, sleeping %d seconds." %
                          ACONF.polling_interval)
                time.sleep(ACONF.polling_interval)
                continue

            # Any updated ports should verify their existence on the network
            # bridge.
            for p in u_ports:
                # TODO(thorst) optimize this path
                dev = self.plugin_rpc.get_device_details(self.context,
                                                         p.get('mac_address'),
                                                         self.agent_id,
                                                         ACONF.pvm_host_mtms)
                phys_net = dev.get('physical_network')
                net_br.ensure_vlan_on_nb(self.api_utils.adapter,
                                         self.api_utils.host_id,
                                         self.br_map.get(phys_net),
                                         dev.get('segmentation_id'))


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
