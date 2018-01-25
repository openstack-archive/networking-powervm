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

from neutron_lib import constants as p_constants
from neutron_lib.plugins.ml2 import api
from oslo_log import log

from networking_powervm.plugins.ibm.agent.powervm import constants as pconst
from networking_powervm.plugins.ml2.drivers import mech_pvm_base

LOG = log.getLogger(__name__)


class PvmSEAMechanismDriver(mech_pvm_base.PvmMechanismDriverBase):
    """Attach to networks using PowerVM Shared Ethernet agent.

    The PvmSEAMechanismDriver integrates the ml2 plugin with the
    PowerVM Shared Ethernet Agent.
    """

    def __init__(self):
        super(PvmSEAMechanismDriver, self).__init__(pconst.AGENT_TYPE_PVM_SEA,
                                                    pconst.VIF_TYPE_PVM_SEA)

    def try_to_bind_segment_for_agent(self, context, segment, agent):
        # When this method is called, the parent should ideally be calling
        # down to the agent to state that the port was updated.  However,
        # it appears this isn't flowing properly.  This makes sure the
        # port is passed down to the agent.
        bindable = (super(PvmSEAMechanismDriver, self).
                    try_to_bind_segment_for_agent(context, segment, agent))
        if bindable:
            self.rpc_publisher.port_update(context._plugin_context,
                                           context._port,
                                           segment[api.NETWORK_TYPE],
                                           segment[api.SEGMENTATION_ID],
                                           segment[api.PHYSICAL_NETWORK])
        return bindable

    def get_allowed_network_types(self, agent=None):
        """Get a list of allowed network types.

        The SEA agent supports only the VLAN network type.

        :param agent: Not used
        :return: List of neutron_lib.constants.TYPE_* strings.
        """
        return [p_constants.TYPE_VLAN]
