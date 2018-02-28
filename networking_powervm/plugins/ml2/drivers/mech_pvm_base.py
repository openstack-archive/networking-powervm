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

import copy

from neutron.plugins.ml2.drivers import mech_agent
from neutron.plugins.ml2 import rpc
from neutron_lib.agent import topics
from neutron_lib.api.definitions import portbindings
from neutron_lib.plugins.ml2 import api
from oslo_log import log

LOG = log.getLogger(__name__)


class PvmMechanismDriverBase(mech_agent.SimpleAgentMechanismDriverBase):
    """Base class for PowerVM mechanism drivers."""

    def __init__(self, agent_type, vif_type, **kwargs):
        super(PvmMechanismDriverBase, self).__init__(
            agent_type, vif_type, {portbindings.CAP_PORT_FILTER: False},
            **kwargs)
        self.rpc_publisher = rpc.AgentNotifierApi(topics.AGENT)

    def try_to_bind_segment_for_agent(self, context, segment, agent):
        """Perform binding operation with agent validation.

        :param context: Context of this transaction.
        :param segment: Neutron network object.
        :param agent: Agent configuration.
        :return bindable: Boolean value if port can be bound.
        """
        # This override is a near-duplicate of the superclass impl - but we
        # need to customize the vif_details.
        if self.check_segment_for_agent(segment, agent):
            context.set_binding(
                segment[api.ID], self.vif_type,
                self.customize_vif_details(context, segment, agent))
            return True

        return False

    def customize_vif_details(self, context, segment, agent):
        """Enhance vif details with any driver impl-specific data.

        :param context: Context of this transaction.
        :param segment: Neutron network object.
        :param agent: Agent configuration.
        :return: A dict containing VIF details.  The base impl just invokes
                 _get_vif_details.
        """
        return self._get_vif_details(segment)

    def get_mappings(self, agent):
        """Get bridge mappings from agent configuration.

        Returns the dict of bridge mappings set by the BasePVMNeutronAgent
        subclass corresponding to this mechanism driver.

        :param agent: BasePVMNeutronAgent subclass instance.
        :return mappings: Mappings provided by corresponding agent.  The format
                          will be specific to the agent - see the agent's
                          parse_bridge_mappings method.
        """
        return agent['configurations'].get('bridge_mappings', {})

    def _get_vif_details(self, segment):
        """Gets customized vif details for this mechanism driver.

        :param segment: Neutron network object.
        :return: A dict containing VIF details, including:
                 - vlan: (Integer) VLAN ID for the vif.  None for flat VLANs.
        """
        vif_details = copy.copy(self.vif_details)
        vlan_id = segment.get(api.SEGMENTATION_ID)
        vif_details[portbindings.VIF_DETAILS_VLAN] = (
            str(vlan_id) if vlan_id is not None else None)
        return vif_details
