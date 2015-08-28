# Copyright 2015 IBM Corp.
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

import copy

import eventlet
eventlet.monkey_patch()

from oslo_config import cfg
from oslo_log import log as logging
from oslo_service import loopingcall

from neutron.agent.common import config as a_config
from neutron.agent import rpc as agent_rpc
from neutron.common import constants as q_const
from neutron.common import topics
from neutron import context as ctx
from neutron.i18n import _, _LW, _LE
from pypowervm import adapter as pvm_adpt
from pypowervm.helpers import log_helper as log_hlp
from pypowervm.helpers import vios_busy as vio_hlp
from pypowervm.utils import uuid as pvm_uuid

from neutron_powervm.plugins.ibm.agent.powervm import utils

import time


LOG = logging.getLogger(__name__)


agent_opts = [
    cfg.IntOpt('polling_interval', default=2,
               help=_("The number of seconds the agent will wait between "
                      "polling for local device changes.")),
    cfg.IntOpt('heal_and_optimize_interval', default=300,
               help=_('The number of seconds the agent should wait between '
                      'heal/optimize intervals.  Should be higher than the '
                      'polling_interval as it runs in the nearest polling '
                      'loop.'))
]

cfg.CONF.register_opts(agent_opts, "AGENT")
a_config.register_agent_state_opts_helper(cfg.CONF)
a_config.register_root_helper(cfg.CONF)

ACONF = cfg.CONF.AGENT


class PVMPluginApi(agent_rpc.PluginApi):
    pass


class PVMRpcCallbacks(object):
    """
    Provides call backs (as defined in the setup_rpc method within the
    appropriate Neutron Agent class) that will be invoked upon certain
    actions from the controller.
    """

    # This agent supports RPC Version 1.0.  Though agents don't boot unless
    # 1.1 or higher is specified now.
    # For reference:
    #  1.0 Initial version
    #  1.1 Support Security Group RPC
    #  1.2 Support DVR (Distributed Virtual Router) RPC
    RPC_API_VERSION = '1.1'

    def __init__(self, agent):
        """
        Creates the call back.  Most of the call back methods will be
        delegated to the agent.

        :param agent: The owning agent to delegate the callbacks to.
        """
        super(PVMRpcCallbacks, self).__init__()
        self.agent = agent

    def port_update(self, context, **kwargs):
        port = kwargs['port']
        self.agent._update_port(port)
        LOG.debug("port_update RPC received for port: %s", port['id'])

    def network_delete(self, context, **kwargs):
        network_id = kwargs.get('network_id')
        LOG.debug("network_delete RPC received for network: %s", network_id)


class ProvisionRequest(object):
    """A request for a Neutron Port to be provisioned.

    The RPC device details provide some additional details that the port does
    not necessarily have, and vice versa.  This meshes together the required
    aspects into a single element.
    """

    def __init__(self, device_detail, port):
        self.segmentation_id = device_detail.get('segmentation_id')
        self.physical_network = device_detail.get('physical_network')
        self.mac_address = device_detail.get('mac_address')
        self.device_owner = device_detail.get('device_owner')
        self.rpc_device = device_detail
        self.device_id = port.get('device_id') if port else None
        self.lpar_uuid = (pvm_uuid.convert_uuid_to_pvm(self.device_id).upper()
                          if self.device_id else None)


def build_prov_requests(devices, ports):
    """Builds the list of ProvisionRequest objects.

    :param devices: The corresponding neutron device details.  Has some unique
                    info like segmentation_id and physical_network.
    :param ports: The 'Neutron Ports'
    :return: A list of the ProvisionRequest objects, which mesh together the
             Port and Device data.
    """
    resp = []
    for port in ports:
        port_uuid = port.get('id')

        # Make sure we have a UUID
        if port_uuid is None:
            continue

        for dev in devices:
            # If the device's id (really the port uuid) doesn't match, ignore
            # it.
            dev_pid = dev.get('port_id')
            if dev_pid is None or port_uuid != dev_pid:
                continue

            # Valid request.  Add it
            resp.append(ProvisionRequest(dev, port))
    return resp


class BasePVMNeutronAgent(object):
    """Baseline PowerVM Neutron Agent class for extension.

    The ML2 agents have a common RPC polling framework and API callback
    mechanism.  This class provides the baseline so that other children
    classes can extend and focus on their specific functions rather than
    integration with the RPC server.
    """

    def __init__(self, binary_name, agent_type):
        self.agent_state = {'binary': binary_name, 'host': cfg.CONF.host,
                            'topic': q_const.L2_AGENT_TOPIC,
                            'configurations': {}, 'agent_type': agent_type,
                            'start_flag': True}
        self.setup_rpc()

        # Create the utility class that enables work against the Hypervisors
        # Shared Ethernet NetworkBridge.
        self.setup_adapter()

        # A list of ports that maintains the list of current 'modified' ports
        self.updated_ports = []

    def setup_adapter(self):
        """Configures the pypowervm adapter and utilities."""
        self.adapter = pvm_adpt.Adapter(
            pvm_adpt.Session(), helpers=[log_hlp.log_helper,
                                         vio_hlp.vios_busy_retry_helper])
        self.host_uuid = utils.get_host_uuid(self.adapter)

    def setup_rpc(self):
        """Registers the RPC consumers for the plugin."""
        self.agent_id = 'sea-agent-%s' % cfg.CONF.host
        self.topic = topics.AGENT
        self.plugin_rpc = PVMPluginApi(topics.PLUGIN)
        self.state_rpc = agent_rpc.PluginReportStateAPI(topics.PLUGIN)

        self.context = ctx.get_admin_context_without_session()

        # Defines what will be listening for incoming events from the
        # controller.
        self.endpoints = [PVMRpcCallbacks(self)]

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
        """
        Reports the state of the agent back to the controller.  Controller
        knows that if a response isn't provided in a certain period of time
        then the agent is dead.  This call simply tells the controller that
        the agent is alive.
        """
        # TODO(thorst) provide some level of devices connected to this agent.
        try:
            device_count = 0
            self.agent_state.get('configurations')['devices'] = device_count
            self.state_rpc.report_state(self.context,
                                        self.agent_state)
            self.agent_state.pop('start_flag', None)
        except Exception:
            LOG.exception(_("Failed reporting state!"))

    def update_device_up(self, device):
        """Calls back to neutron that a device is alive."""
        self.plugin_rpc.update_device_up(self.context, device['device'],
                                         self.agent_id, cfg.CONF.host)

    def update_device_down(self, device):
        """Calls back to neutron that a device is down."""
        self.plugin_rpc.update_device_down(self.context, device['device'],
                                           self.agent_id, cfg.CONF.host)

    def _update_port(self, port):
        """Invoked to indicate that a port has been updated within Neutron."""
        self.updated_ports.append(port)

    def _list_updated_ports(self):
        """
        Will return (and then reset) the list of updated ports received
        from the system.
        """
        ports = copy.copy(self.updated_ports)
        self.updated_ports = []
        return ports

    def heal_and_optimize(self, is_boot):
        """Ensures that the bridging supports all the needed ports.

        This method is invoked periodically (not on every RPC loop).  Its
        purpose is to ensure that the bridging supports every client VM
        properly.  If possible, it should also optimize the connections.

        :param is_boot: Indicates if this is the first call on boot up of the
                        agent.
        """
        raise NotImplementedError()

    def provision_devices(self, requests):
        """Invoked when a set of new Neutron ports has been detected.

        This method should provision the bridging for the new devices

        Must be implemented by a subclass.

        The subclass implementation may be non-blocking.  This means, if it
        will take a very long time to provision, or has a dependency on
        another action (ex. client VIF needs to be created), then it should
        run in a separate worker thread.

        Because of the non-blocking nature of the method, it is required that
        the child class updates the device state upon completion of the device
        provisioning.  This can be done with the agent's
        update_device_up/_down methods.

        :param requests: A list of ProvisionRequest objects.
        """
        raise NotImplementedError()

    def rpc_loop(self):
        """
        Runs a check periodically to determine if new ports were added or
        removed.  Will call down to appropriate methods to determine correct
        course of action.
        """

        loop_timer = float(0)
        loop_interval = float(ACONF.heal_and_optimize_interval)
        first_loop = True
        succesive_exceptions = 0

        while True:
            try:
                # If the loop interval has passed, heal and optimize
                if time.time() - loop_timer > loop_interval:
                    LOG.debug("Performing heal and optimization of system.")
                    self.heal_and_optimize(first_loop)
                    first_loop = False
                    loop_timer = time.time()

                # Determine if there are new ports
                u_ports = self._list_updated_ports()

                # If there are no updated ports, just sleep and re-loop
                if not u_ports:
                    LOG.debug("No changes, sleeping %d seconds.",
                              ACONF.polling_interval)
                    time.sleep(ACONF.polling_interval)
                    continue

                # Provision the ports on the Network Bridge.
                self.attempt_provision(u_ports)

                # If the code reached this point, the successive exceptions
                # can be reset to 0.
                succesive_exceptions = 0
            except Exception as e:
                # The agent should retry a few times, in case something
                # bubbled up.  A successful provision loop will reset the
                # timer.
                #
                # Note that the exception timer is not reset if there are no
                # provisions.  That is because 99% of the errors will occur
                # in the provision path.  So we only reset the error when
                # a successful provision has occurred (otherwise we'd never
                # hit the exception limit).
                succesive_exceptions += 1
                LOG.exception(e)
                if succesive_exceptions == 3:
                    LOG.error(_LE("Multiple exceptions have been "
                                  "encountered.  The agent is unable to "
                                  "proceed.  Exiting."))
                    raise
                else:
                    LOG.warn(_LW("Error has been encountered and logged.  The "
                             "agent will retry again."))

    def attempt_provision(self, ports):
        """Attempts the provisioning of the updated ports.

        This method will attempt to provision a set of ports (by wrapping
        around the provision_ports method).  If there are issues with
        provisioning the ports, this method will update the status in the
        backing Neutron server.

        :param ports: The list of ports to provision.
        """
        # Convert the ports to devices.
        dev_list = [x.get('mac_address') for x in ports]
        devs = self.plugin_rpc.get_devices_details_list(
            self.context, dev_list, self.agent_id)

        # Build the network devices
        provision_requests = build_prov_requests(devs, ports)

        try:
            LOG.debug("Provisioning ports for mac addresses [ %s ]" %
                      ' '.join(dev_list))
            self.provision_devices(provision_requests)
        except Exception:
            # Set the state of the device as 'down'
            for p_req in provision_requests:
                self.update_device_down(p_req.rpc_device)

            # Reraise the exception
            raise
