# Copyright 2016 IBM Corp.
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
import sys
import time

try:
    import queue
except ImportError:
    import Queue as queue

from neutron.common import config as n_config
from oslo_config import cfg
from oslo_log import log as logging

from networking_powervm._i18n import _LI
from networking_powervm._i18n import _LW
from networking_powervm.plugins.ibm.agent.powervm import agent_base
from networking_powervm.plugins.ibm.agent.powervm import constants as p_const
from pypowervm import util as pvm_util
from pypowervm.wrappers import iocard as pvm_card
from pypowervm.wrappers import logical_partition as pvm_lpar
from pypowervm.wrappers import managed_system as pvm_ms

eventlet.monkey_patch()


LOG = logging.getLogger(__name__)

ACONF = cfg.CONF.AGENT

# Time out waiting for a port's VIF to be plugged after 20 minutes.
PORT_TIMEOUT_S = 20 * 60


def port_timed_out(port):
    """Determine if we should stop waiting for this port's vNIC to appear.

    The port's 'updated_at' value gets set when neutron assigns the port to
    be plugged into an instance.

    :param port: The port dict.  This method uses the 'updated_at' key.
    :return: True if the port was 'updated' more than PORT_TIMEOUT_S
             seconds ago.  False otherwise.
    """
    then = port['update_received_at']
    now = time.time()
    return now - then > PORT_TIMEOUT_S


class SRIOVNeutronAgent(agent_base.BasePVMNeutronAgent):
    """
    Provides VLAN networks for the PowerVM platform that run through
    shared-mode SR-IOV adapters within the Virtual I/O Servers in the form of
    vNIC.  Designed to be compatible with the ML2 Neutron Plugin.
    """

    @property
    def agent_id(self):
        return 'sriov-agent-%s' % cfg.CONF.host

    def customize_agent_state(self):
        """Set SR-IOV-specific configurations in the agent_state."""
        self.agent_state['configurations']['default_redundancy'] = (
            ACONF.vnic_required_vfs)
        self.agent_state['configurations']['default_capacity'] = (
            ACONF.vnic_vf_capacity)

    def __init__(self):
        """Constructs the agent."""
        name = 'networking-powervm-sriov-agent'
        agent_type = p_const.AGENT_TYPE_PVM_SRIOV
        self._msys = None
        # Synchronized FIFO of port updates
        self._port_update_queue = queue.Queue()
        super(SRIOVNeutronAgent, self).__init__(name, agent_type)

    def _update_port(self, port):
        LOG.info(_LI('Pushing updated port with mac %s'),
                 port.get('mac_address', '<unknown>'))
        # Stamp it so we can time it out
        port['update_received_at'] = time.time()
        self._port_update_queue.put(port)

    @property
    def msys(self):
        if self._msys is None:
            self._msys = pvm_ms.System.get(self.adapter)[0]
        else:
            self._msys = self._msys.refresh()
        return self._msys

    def parse_bridge_mappings(self):
        """Dict of {physnet: [physloc, ...]} for SR-IOV physical ports.

        The physical network name is retrieved from the SR-IOV physical port's
        label.  The user is responsible for setting the label prior to agent
        activation.  Unlabeled ports will be assumed to belong to the 'default'
        network.

        :return mapping: Return a mapping of physical network names to lists of
                         SR-IOV physical port location codes.  Example:
                         {'default': ['U78C9.001.WZS094N-P1-C7-T2',
                                      'U78C9.001.WZS094N-P2-C1-T3'],
                          'prod': ['U78C9.001.WZS094N-P2-C7-T2',
                                   'U78C9.001.WZS094N-P1-C1-T3',
                                   'U78C9.001.WZS094N-P1-C3-T1']}
        """
        mapping = {}
        for sriov in self.msys.asio_config.sriov_adapters:
            for pport_w in sriov.phys_ports:
                label = pport_w.label or 'default'
                if label not in mapping:
                    mapping[label] = []
                mapping[label].append(pport_w.loc_code)
        return mapping

    def is_vif_plugged(self, port):
        """Detect whether the vif associated with the port has been plugged in.

        :param port: Port dict associated with the vif in question.
        :return: Boolean (or boolean-evaluable) True if the port's vif is
                 plugged; False otherwise.
        """
        # TODO(efried): Replace this slow-and-heavy poll with EventHandler
        mac = pvm_util.sanitize_mac_for_api(port['mac_address'])
        return pvm_card.VNIC.search(self.adapter, parent_type=pvm_lpar.LPAR,
                                    mac=mac)

    def rpc_loop(self):
        while True:
            # Refresh the label:physloc mappings.  This must remain atomic, or
            # be synchronized with agent_base._report_state.
            self.agent_state['configurations']['bridge_mappings'] = (
                self.parse_bridge_mappings())

            # Report activation of any new ports
            while True:
                try:
                    port = self._port_update_queue.get(block=False)
                    if port_timed_out(port):
                        LOG.warning(_LW("Timed out looking for vNIC with MAC "
                                        "%s.  Not setting device_up."),
                                    port['mac_address'])
                        continue
                    # Wait to activate the device until the vif is plugged
                    if self.is_vif_plugged(port):
                        self.update_device_up(self.get_device_details(
                            port['mac_address']))
                    else:
                        # Requeue the port to check next iteration.
                        self._port_update_queue.put(port)
                except queue.Empty:
                    # No more updates right now
                    break

            LOG.debug("Sleeping %d seconds.", ACONF.polling_interval)
            time.sleep(ACONF.polling_interval)


def main():
    # Read in the command line args
    n_config.init(sys.argv[1:])
    n_config.setup_logging()

    # Build then run the agent
    agent = SRIOVNeutronAgent()
    LOG.info(_LI("PowerVM SR-IOV Agent initialized and running."))
    agent.rpc_loop()


if __name__ == "__main__":
    main()
