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

from oslo_log import log

from neutron.common import topics
from neutron.extensions import portbindings
from neutron.plugins.common import constants as p_constants
from neutron.plugins.ml2 import driver_api as api
from neutron.plugins.ml2.drivers import mech_agent
from neutron.plugins.ml2 import rpc

from networking_powervm.plugins.ibm.agent.powervm import constants as pconst

LOG = log.getLogger(__name__)


class PvmSEAMechanismDriver(mech_agent.SimpleAgentMechanismDriverBase):
    """Attach to networks using PowerVM Shared Ethernet agent.

    The PvmSEAMechanismDriver integrates the ml2 plugin with the
    PowerVM Shared Ethernet Agent.
    """

    def __init__(self):
        super(PvmSEAMechanismDriver, self).__init__(
            pconst.AGENT_TYPE_PVM_SEA,
            pconst.VIF_TYPE_PVM_SEA,
            {portbindings.CAP_PORT_FILTER: False})
        self.rpc_publisher = rpc.AgentNotifierApi(topics.AGENT)

    def check_segment_for_agent(self, segment, agent):
        # TODO(thorst) Remove this in OpenStack Newton.  It can be assumed that
        # all agents are properly returning the mappings at that time.  The
        # agents started reporting the mappings in Mitaka.
        if self.get_mappings(agent):
            return (super(PvmSEAMechanismDriver, self).
                    check_segment_for_agent(segment, agent))
        else:
            LOG.debug("Checking segment: %s", segment)
            network_type = segment[api.NETWORK_TYPE]
            return network_type in ['vlan']

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
        return [p_constants.TYPE_VLAN]

    def get_mappings(self, agent):
        return agent['configurations'].get('bridge_mappings', {})
