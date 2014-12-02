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


from neutron.extensions import portbindings
from neutron.openstack.common import log
from neutron.plugins.ml2 import driver_api as api
from neutron.plugins.ml2.drivers import mech_agent

from neutron_powervm.plugins.ibm.agent.powervm import constants as pconst

LOG = log.getLogger(__name__)


class PvmSEAMechanismDriver(mech_agent.SimpleAgentMechanismDriverBase):
    """Attach to networks using PowerVM Shared Ethernet agent.

    The PvmSEAMechanismDriver integrates the ml2 plugin with the
    PowerVM Shared Ethernet Agent.
    """

    def __init__(self):
        # TODO(thorst) these need to be evaluated for entry into Neutron core
        super(PvmSEAMechanismDriver, self).__init__(
            pconst.AGENT_TYPE_PVM_SEA,
            pconst.VIF_TYPE_PVM_SEA,
            {portbindings.CAP_PORT_FILTER: False})

    def check_segment_for_agent(self, segment, agent):
        # TODO(thorst) Define appropriate mapping.  Determine whether
        # this VLAN / segment can be supported by the agent.
        LOG.debug("Checking segment: %(segment)s" % {'segment': segment})
        network_type = segment[api.NETWORK_TYPE]
        return network_type in ['vlan']
