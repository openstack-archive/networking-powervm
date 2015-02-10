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
from neutron.openstack.common import log as logging
from neutron.openstack.common import loopingcall

from neutron_powervm.plugins.ibm.agent.powervm import constants as p_const
from neutron_powervm.plugins.ibm.agent.powervm import utils as pvm_utils

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
               help='The password for authentication into the API.')
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
        self.conn_utils = pvm_utils.NetworkBridgeUtils(ACONF.pvm_server_ip,
                                                       ACONF.pvm_user_id,
                                                       password,
                                                       ACONF.pvm_host_mtms)

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

        # Define the listening consumers for the agent
        # TODO(thorst) We may just want to change this to port/update,
        # port/create, and port/delete.  Then plug on those?
        consumers = [[topics.PORT, topics.UPDATE],
                     [topics.NETWORK, topics.DELETE]]

        self.connection = agent_rpc.create_consumers(self.endpoints,
                                                     self.topic,
                                                     consumers)

        report_interval = cfg.CONF.AGENT.report_interval
        if report_interval:
            heartbeat = loopingcall.FixedIntervalLoopingCall(
                    self._report_state)
            heartbeat.start(interval=report_interval)

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

    def _scan_port_delta(self, updated_ports):
        '''
        Determines from the updated_ports list which ports are new, which are
        removed, and which are unchanged.

        :param updated_ports: Ports that were detected as updated from the
                              Neutron Server.
        :returns: Dictionary of the input split into a set of 'added',
                  'removed' and 'updated' ports.
        '''
        # Step 1: List all of the ports on the system.
        client_adpts = self.conn_utils.list_client_adpts()

        a_ports = []
        u_ports = []
        r_ports = []

        # Step 2: For each updated port, determine if it is new or updated
        for u_port in updated_ports:
            c_na = self.conn_utils.find_client_adpt_for_mac(
                    u_port.get('mac_address'), client_adpts)
            if c_na is None:
                a_ports.append(u_port)
            else:
                u_ports.append(u_port)

        # TODO(thorst) Step 3: Determine removed ports

        # Return the results
        return {'added': a_ports, 'removed': r_ports,
                'updated': u_ports}

    def rpc_loop(self):
        '''
        Runs a check periodically to determine if new ports were added or
        removed.  Will call down to appropriate methods to determine correct
        course of action.
        '''
        while True:
            # Determine if there are new ports
            u_ports = self._list_updated_ports()

            # If there are no updated ports, just sleep and re-loop
            if not u_ports:
                # TODO(thorst) reconcile the wait timer down to a method
                LOG.debug("No changes, sleeping")
                time.sleep(5)
                continue

            # TODO(thorst) mainline logic will go here
            self._scan_port_delta(u_ports)

            time.sleep(1)


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
