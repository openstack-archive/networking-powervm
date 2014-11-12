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

import eventlet
eventlet.monkey_patch()

from oslo.config import cfg

from neutron.agent.common import config as a_config
from neutron.agent import rpc as agent_rpc
from neutron.common import config as n_config
from neutron.common import constants as q_const
from neutron.common import rpc as n_rpc
from neutron.common import topics
from neutron import context as ctx
from neutron.openstack.common import log as logging
from neutron.openstack.common import loopingcall

from neutron_powervm.plugins.ibm.agent.powervm import utils as pvm_utils

import sys
import time


LOG = logging.getLogger(__name__)


agent_opts = [
    cfg.IntOpt('polling_interval', default=2,
               help=_("The number of seconds the agent will wait between "
                      "polling for local device changes.")),
    # TODO(thorst) Remove when Neo is running on locally
    cfg.StrOpt('hmc_host', default="9.114.181.230",
               help=_("The HMC that is managing the system.")),
    cfg.StrOpt('hmc_user', default="hscroot",
               help=_("The user id to use for the HMC communication.")),
    cfg.StrOpt('hmc_pass', default="Passw0rd",
               help=_("The password to the HMC User ID.")),
    cfg.StrOpt('system_uuid', default="726e9cb3-6576-3df5-ab60-40893d51d074",
               help=_("The system UUID that the agent should operate "
                      "against."))
]


cfg.CONF.register_opts(agent_opts, "AGENT")
a_config.register_agent_state_opts_helper(cfg.CONF)
a_config.register_root_helper(cfg.CONF)

ACONF = cfg.CONF.AGENT


class SharedEthernetPluginApi(agent_rpc.PluginApi):
    pass


class SharedEthernetRpcCallbacks(n_rpc.RpcCallback):
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
        port_id = kwargs['port']['id']

        # TODO Need to perform the call back
        LOG.debug(_("port_update RPC received for port: %s"), port_id)

    def network_delete(self, context, **kwargs):
        network_id = kwargs.get('network_id')

        # TODO Need to perform the call back
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
            'agent_type': 'PowerVM Shared Ethernet agent',
            'start_flag': True}
        self.setup_rpc()

        # Create the utility class that enables work against the Hypervisors
        # Shared Ethernet NetworkBridge.
        self.conn_utils = pvm_utils.NetworkBridgeUtils(ACONF.hmc_host,
                                                       ACONF.hmc_user,
                                                       ACONF.hmc_pass,
                                                       ACONF.system_uuid)

        # Attempt a list of the Network Bridges to validate connection
        self.conn_utils.list_bridges()

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
        # TODO We may just want to change this to port/update,
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
        # TODO provide some level of devices connected to this agent.
        try:
            device_count = 0
            self.agent_state.get('configurations')['devices'] = device_count
            self.state_rpc.report_state(self.context,
                                        self.agent_state)
            self.agent_state.pop('start_flag', None)
        except Exception:
            LOG.exception(_("Failed reporting state!"))

    def rpc_loop(self):
        '''
        Runs a check periodically to determine if new ports were added or
        removed.  Will call down to appropriate methods to determine correct
        course of action.
        '''
        # TODO(thorst) implement
        while True:
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
