# Copyright 2014, 2018 IBM Corp.
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

from neutron_lib.api.definitions import portbindings
from neutron_lib import constants as p_constants
from oslo_log import log

from networking_powervm.plugins.ibm.agent.powervm import constants as pconst
from networking_powervm.plugins.ml2.drivers import mech_pvm_base

LOG = log.getLogger(__name__)


class PvmSRIOVMechanismDriver(mech_pvm_base.PvmMechanismDriverBase):
    """Attach to networks using PowerVM SR-IOV agent.

    The PvmSRIOVMechanismDriver integrates the ml2 plugin with the
    PowerVM SRIOV Agent.
    """

    def __init__(self):
        super(PvmSRIOVMechanismDriver, self).__init__(
            pconst.AGENT_TYPE_PVM_SRIOV, pconst.VIF_TYPE_PVM_SRIOV,
            supported_vnic_types=[portbindings.VNIC_DIRECT])

    def get_allowed_network_types(self, agent=None):
        """Get a list of allowed network types.

        This mechanism driver supports only VLAN and FLAT network types.

        :param agent: Not used
        :return: List of neutron_lib.constants.TYPE_* strings.
        """
        return [p_constants.TYPE_FLAT, p_constants.TYPE_VLAN]

    def customize_vif_details(self, context, segment, agent):
        """Gets customized vif details for this mechanism driver.

        :param context: Context of this transaction.
        :param segment: Neutron network object.
        :param agent: SR-IOV vNIC agent
        :return: A dict containing VIF details, including the following keys:
                 - vlan: (Integer) VLAN ID for the vif.
                 - physical_ports: (List(str)) List of physical location codes
                   of SR-IOV physical ports cabled to the neutron network for
                   this vif (based on matching the port labels to the network
                   name).
                 - default_redundancy: (Integer) Default number of VFs with
                   which to back new vNICs.  May be overridden by
                   binding:profile['vnic_required_vfs'] at plug time.
        """
        vif_details = (
            super(PvmSRIOVMechanismDriver, self).customize_vif_details(
                context, segment, agent))
        vif_details['physical_ports'] = self.get_mappings(agent).get(
            segment['physical_network'], [])
        vif_details['physical_network'] = segment['physical_network']
        profile = context.current.get(portbindings.PROFILE, {})
        # TODO(efried): binding:profile info is not in the 'profile' var!
        # Redundancy: from binding:profile or the ml2 conf.
        vif_details['redundancy'] = int(profile.get(
            'vnic_required_vfs',
            agent['configurations']['default_redundancy']))
        # Capacity: from binding:profile or the ml2 conf.  If unspecified in
        # either, let the platform default.
        cap = profile.get(
            'capacity', agent['configurations']['default_capacity'])
        try:
            vif_details['capacity'] = float(cap)
        except (TypeError, ValueError):
            # cap may be None or 'None' at this point, depending on the source.
            vif_details['capacity'] = None

        # Max Capacity: from binding:profile. If not specified, it is
        # set to None
        maxcap = profile.get('maxcapacity')
        try:
            vif_details['maxcapacity'] = float(maxcap)
        except (TypeError, ValueError):
            # cap may be None or 'None' at this point, depending on the source.
            vif_details['maxcapacity'] = None
        return vif_details
