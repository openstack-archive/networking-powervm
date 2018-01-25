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
"""ProvisionRequest class and related artifacts."""
import time

from neutron.conf.agent import common as a_config
from oslo_config import cfg
from oslo_log import log as logging
from oslo_serialization import jsonutils
from pypowervm import util as pvm_util
from pypowervm.wrappers import event as pvm_evt

from networking_powervm.plugins.ibm.agent.powervm import utils

a_config.register_agent_state_opts_helper(cfg.CONF)
a_config.register_root_helper(cfg.CONF)

LOG = logging.getLogger(__name__)

# Time out waiting for a port's VIF to be plugged after 20 minutes.
PORT_TIMEOUT_S = 20 * 60

# Provisioning actions
PLUG = 'plug'
UNPLUG = 'unplug'

# Event provider ID for nova_powervm.virt.powervm.vif
EVENT_PROVIDER_NOVA_PVM_VIF = 'NOVA_PVM_VIF'


class ProvisionRequest(object):
    """A request for a Neutron Port to be provisioned.

    The RPC device details provide some additional details that the port does
    not necessarily have, and vice versa.  This meshes together the required
    aspects into a single element.
    """

    def __init__(self, action, device_detail, lpar_uuid, vif_type=None):
        """Create a ProvisionRequest for a neutron device associated with a VM.

        Consumers should not call this directly, but should instead use one of
        the factory methods: for_wrappers, for_event, or for_port.

        :param action: One of PLUG or UNPLUG, indicating whether this request
                       should result in the device being marked up or down.
        :param device_detail: The neutron device detail dict returned from
                              the agent's get_device[s]_details[_list].  Should
                              be pre-validated (see utils.device_detail_valid).
        :param lpar_uuid: String UUID of the Logical Partition associated with
                          the device, in PowerVM format (see
                          pypowervm.utils.uuid.convert_uuid_to_pvm).
        :param vif_type: Source of event. It could be pvm_sea or pvm_sriov or
                         others
        """
        self.action = action
        self.mac_address = device_detail.get('mac_address')
        self.rpc_device = device_detail
        self.lpar_uuid = lpar_uuid
        self.created_at = time.time()
        self.vif_type = vif_type

    def __eq__(self, other):
        if not isinstance(other, ProvisionRequest):
            return False

        # Really just need to check the lpar_uuid and mac.  The rest should
        # be static and identical.
        return (other.mac_address == self.mac_address and
                other.lpar_uuid == self.lpar_uuid)

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        # Mac addresses should not collide.  This should be sufficient for a
        # hash.  The equals will go just a bit further.
        return hash(self.mac_address)

    def __str__(self):
        return ("ProvisionRequest(action=%(action)s, mac=%(mac)s, "
                "lpar_uuid=%(lpar_uuid)s)" % {'action': self.action,
                                              'mac': self.mac_address,
                                              'lpar_uuid': self.lpar_uuid})

    @classmethod
    def for_wrappers(cls, agent, lpar_vif_map, action):
        """Factory method to produce ProvisionRequests from VIF wrappers.

        :param agent: The neutron agent making the request.
        :param lpar_vif_map: Dict of {lpar_uuid: [vif_w, ...]), where lpar_uuid
                             is the UUID of the LPAR owning the VIF; and each
                             vif_w is a pypowervm wrapper of a VIF-ish type
                             (CNA, VNIC, etc.)
        :param action: What kind of provision requests, either PLUG or UNPLUG.
        :return: A list of new ProvisionRequest instances for each "valid"
                 LPAR/VIF tuple in lpar_vif_list.  Here "valid" means the VIF's
                 corresponding device can be found in neutron, and its instance
                 match the LPAR.
        """
        # Dict mapping {mac: device_detail}
        macs = [vif.mac for vifs in lpar_vif_map.values() for vif in vifs]
        device_details = {dev.get('mac_address'): dev for dev in
                          agent.get_devices_details_list(macs)}
        ret = []
        for lpar, viflist in lpar_vif_map.items():
            for vif_w in viflist:
                mac = utils.norm_mac(vif_w.mac)
                if mac not in device_details:
                    # A VIF with no corresponding neutron port
                    continue
                detail = device_details[mac]
                if not utils.device_detail_valid(detail, mac):
                    continue
                LOG.info(
                    "Creating wrapper-based %(action)s ProvisionRequest for "
                    "%(vif_type)s VIF with MAC %(mac)s associated with LPAR "
                    "%(lpar_name)s (%(lpar_uuid)s).",
                    {'action': action, 'vif_type': vif_w.schema_type,
                     'mac': vif_w.mac, 'lpar_name': lpar.name,
                     'lpar_uuid': lpar.uuid})
                ret.append(cls(action, detail, lpar.uuid))

        return ret

    @classmethod
    def for_event(cls, agent, event):
        """Factory method to produce a ProvisionRequest for an Event.

        :param agent: The neutron agent making the request.
        :param event: pypowervm.wrappers.event.Event to be processed.
        :return: A new ProvisionRequest.  Returns None if the event is not of
                 interest to the agent.  If the event indicates a PLUG, returns
                 None if a corresponding device can't be found in neutron.
        """
        # Today, we're only handling CUSTOM_CLIENT_EVENTS provided by
        # nova-powervm's vif driver.  In the future, if PowerVM provides
        # official events for VIF types (CNA, VNIC, etc.), this method can be
        # converted to use them.
        if event.etype != pvm_evt.EventType.CUSTOM_CLIENT_EVENT:
            return None
        try:
            edetail = jsonutils.loads(event.detail)
        except (ValueError, TypeError):
            # Not a custom event we recognize
            return None
        if edetail.get('provider') != EVENT_PROVIDER_NOVA_PVM_VIF:
            # Not provided by nova-powervm's vif driver
            return None

        # The actions in the event should match our PLUG/UNPLUG consts, but
        # account for mismatched future versions
        action = edetail['action']
        if action not in (PLUG, UNPLUG):
            LOG.debug("Ignoring event due to unhandled 'action' type.  %s",
                      str(event))
            return None

        device_detail = agent.get_device_details(edetail['mac'])
        if not utils.device_detail_valid(device_detail, edetail['mac']):
            # device_detail_valid logged why
            return None

        # The event data is the URI.  For this kind of event, it looks like:
        # .../LogicalPartition/<LPAR_UUID>/VirtualNICDedicated/<vnic_uuid>
        lpar_uuid = pvm_util.get_req_path_uuid(event.data, preserve_case=True,
                                               root=True)
        vif_type = edetail['type']
        if agent.vif_type != vif_type:
            return None

        LOG.info("Creating event-based %(action)s ProvisionRequest for VIF "
                 "%(uri)s with MAC %(mac)s associated with LPAR %(lpar_uuid)s "
                 "and source %(vif_type)s.",
                 {'action': edetail['action'], 'uri': event.data,
                  'mac': edetail['mac'], 'lpar_uuid': lpar_uuid,
                  'vif_type': vif_type})
        return cls(edetail['action'], device_detail, lpar_uuid, vif_type)
