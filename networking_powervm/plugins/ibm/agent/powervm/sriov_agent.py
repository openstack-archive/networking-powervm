# Copyright 2016, 2017 IBM Corp.
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

from networking_powervm._i18n import _
from networking_powervm.plugins.ibm.agent.powervm import agent_base
from networking_powervm.plugins.ibm.agent.powervm import constants as p_const
from networking_powervm.plugins.ibm.agent.powervm import prov_req as preq
from networking_powervm.plugins.ibm.agent.powervm import utils
from pypowervm.wrappers import iocard as pvm_card

eventlet.monkey_patch()


LOG = logging.getLogger(__name__)

agent_opts = [
    cfg.IntOpt('vnic_required_vfs', default=2, min=1,
               help=_('Redundancy level for SR-IOV backed vNIC attachments. '
                      'Minimum value is 1.')),
    cfg.FloatOpt('vnic_vf_capacity',
                 help=_("Float up to 4dp between 0.0000 and 1.0000 indicating "
                        "the minimum guaranteed capacity of the VFs backing "
                        "an SR-IOV vNIC.  Must be a multiple of each physical "
                        "port's minimum capacity granularity.  If omitted, "
                        "defaults to the minimum capacity granularity for "
                        "each port."))
]

cfg.CONF.register_opts(agent_opts, "AGENT")
a_config.register_agent_state_opts_helper(cfg.CONF)
a_config.register_root_helper(cfg.CONF)

ACONF = cfg.CONF.AGENT

VIF_TYPE_PVM_SRIOV = 'pvm_sriov'


class SRIOVNeutronAgent(agent_base.BasePVMNeutronAgent):
    """Provides VLANs for vNICs (shared-mode SR-IOV VFs via VIOS).

    Designed to be compatible with the ML2 Neutron Plugin.
    """
    @property
    def agent_id(self):
        return 'sriov-agent-%s' % cfg.CONF.host

    @property
    def agent_binary_name(self):
        """Name of the executable under which the SR-IOV agent runs."""
        return p_const.AGENT_BIN_SRIOV

    @property
    def agent_type(self):
        return p_const.AGENT_TYPE_PVM_SRIOV

    @property
    def vif_wrapper_class(self):
        return pvm_card.VNIC

    @property
    def vif_type(self):
        return VIF_TYPE_PVM_SRIOV

    def customize_agent_state(self):
        """Set SR-IOV-specific configurations in the agent_state."""
        self.agent_state['configurations']['default_redundancy'] = (
            ACONF.vnic_required_vfs)
        self.agent_state['configurations']['default_capacity'] = (
            ACONF.vnic_vf_capacity)

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
        self.msys = self.msys.refresh()
        mapping = {}
        for sriov in self.msys.asio_config.sriov_adapters:
            for pport_w in sriov.phys_ports:
                label = pport_w.label or 'default'
                if label not in mapping:
                    mapping[label] = []
                mapping[label].append(pport_w.loc_code)
        return mapping

    def _refresh_bridge_mappings_to_neutron(self):
        """Refresh the label:physloc mappings and report to neutron."""
        LOG.debug("Refreshing bridge mappings to neutron.  Before: %s",
                  self.agent_state['configurations']['bridge_mappings'])
        nbm = self.parse_bridge_mappings()
        self.agent_state['configurations']['bridge_mappings'] = nbm
        LOG.debug("After: %s", nbm)
        self._report_state()

    def port_update(self, context, **kwargs):
        """Refresh neutron bridge mappings on port update."""
        self._refresh_bridge_mappings_to_neutron()

    def heal_and_optimize(self):
        """Ensure bridge mappings are current, and all VNICs are marked up. """
        self._refresh_bridge_mappings_to_neutron()

        # Create ProvisionRequests for all VNICs on all (non-management client)
        # partitions...
        lpar_vnic_map = utils.list_vifs(self.adapter, self.vif_wrapper_class)
        prov_reqs = preq.ProvisionRequest.for_wrappers(self, lpar_vnic_map,
                                                       preq.PLUG)
        # ...and mark them 'up' in neutron.
        self.provision_devices(prov_reqs)

    def is_hao_event(self, evt):
        """Determines if an Event warrants a heal_and_optimize.

        :param evt: A pypowervm.wrappers.event.Event wrapper to inspect.
        :return: True if heal_and_optimize should be invoked as a result of
                 this event; False otherwise.
        """
        return evt.detail and 'SRIOVPhysicalPort.ConfigChange' in evt.detail


def main():
    # Read in the command line args
    n_config.init(sys.argv[1:])
    n_config.setup_logging()

    # Build then run the agent
    agent = SRIOVNeutronAgent()
    LOG.info("PowerVM SR-IOV Agent initialized and running.")
    agent.rpc_loop()


if __name__ == "__main__":
    main()
