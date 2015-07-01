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

import copy

import eventlet
eventlet.monkey_patch()

from oslo_config import cfg
from oslo_log import log as logging
from oslo_service import loopingcall

from neutron.agent.common import config as a_config
from neutron.agent import rpc as agent_rpc
from neutron.common import config as n_config
from neutron.common import constants as q_const
from neutron.common import topics
from neutron import context as ctx
from neutron.i18n import _, _LE, _LI

from neutron_powervm.plugins.ibm.agent.powervm import constants as p_const

import sys
import time


LOG = logging.getLogger(__name__)


agent_opts = [
    cfg.IntOpt('polling_interval', default=2,
               help=_("The number of seconds the agent will wait between "
                      "polling for local device changes.")),
]


cfg.CONF.register_opts(agent_opts, "AGENT")
a_config.register_agent_state_opts_helper(cfg.CONF)
a_config.register_root_helper(cfg.CONF)

ACONF = cfg.CONF.AGENT


class FakeSharedEthernetPluginApi(agent_rpc.PluginApi):
    pass


class FakeSharedEthernetRpcCallbacks(object):
    '''
    Provides call backs (as defined in the setup_rpc method within the
    FakeSharedEthernetNeutronAgent class) that will be invoked upon certain
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
        super(FakeSharedEthernetRpcCallbacks, self).__init__()
        self.agent = agent

    def port_update(self, context, **kwargs):
        port = kwargs['port']
        self.agent._update_port(port)
        LOG.debug("port_update RPC received for port: %s", port['id'])

    def network_delete(self, context, **kwargs):
        network_id = kwargs.get('network_id')
        LOG.debug("network_delete RPC received for network: %s", network_id)


class FakeSharedEthernetNeutronAgent():
    '''
    This agent provides a simulation baseline that mirrors the baseline
    Shared Ethernet Neutron Agent, but can run independently.  The intent is
    to mirror the purpose of the fake driver in Nova.

    Timeouts and RPC callbacks should mirror that of the standard agent.
    As development is proceeding with the main agent, this class will need
    to be updated as well.
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

    def setup_rpc(self):
        '''
        Registers the RPC consumers for the plugin.
        '''
        self.agent_id = 'sea-agent-%s' % cfg.CONF.host
        self.topic = topics.AGENT
        self.plugin_rpc = FakeSharedEthernetPluginApi(topics.PLUGIN)
        self.state_rpc = agent_rpc.PluginReportStateAPI(topics.PLUGIN)

        self.context = ctx.get_admin_context_without_session()

        # Defines what will be listening for incoming events from the
        # controller.
        self.endpoints = [FakeSharedEthernetRpcCallbacks(self)]

        # Define the listening consumers for the agent
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
        # TODO(jarnold) provide some level of devices connected to this agent.
        try:
            device_count = 0
            self.agent_state.get('configurations')['devices'] = device_count
            self.state_rpc.report_state(self.context,
                                        self.agent_state)
            self.agent_state.pop('start_flag', None)
        except Exception:
            LOG.exception(_LE("Failed reporting state!"))

    def _update_port(self, port):
        '''
        Invoked to indicate that a port has been updated within Neutron.
        '''
        self.updated_ports.add(port)

    def _list_updated_ports(self):
        '''
        Will return (and then reset) the list of updated ports received
        from the system.
        '''
        ports = copy.copy(self.updated_ports)
        self.updated_ports = set()
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
        # TODO(jarnold) Will need to keep track of which ports were previously
        # added.

        # Return the results
        return {'added': updated_ports, 'removed': None,
                'updated': None}

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
                # TODO(jarnold) reconcile the wait timer down to a method
                LOG.debug("No changes, sleeping")
                time.sleep(1)
                continue

            # TODO(jarnold) Post determining ports, appropriate simulation
            # is needed.
            self._scan_port_delta(u_ports)

            time.sleep(1)


def main():
    # Read in the command line args
    n_config.init(sys.argv[1:])
    n_config.setup_logging()

    # Build then run the agent
    agent = FakeSharedEthernetNeutronAgent()
    LOG.info(_LI("Simulated Shared Ethernet Agent initialized and running"))
    agent.rpc_loop()


if __name__ == "__main__":
    main()
