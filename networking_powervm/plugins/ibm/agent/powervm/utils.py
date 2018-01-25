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
"""Provides a set of utilities for API interaction and Neutron."""

import time

from oslo_log import log as logging

from pypowervm import const as pvm_const
from pypowervm import exceptions as pvm_exc
from pypowervm.helpers import log_helper as pvm_log
from pypowervm.tasks import partition as pvm_par
from pypowervm import util as pvm_util
from pypowervm.utils import retry as pvm_retry
from pypowervm.wrappers import logical_partition as pvm_lpar
from pypowervm.wrappers import managed_system as pvm_ms
from pypowervm.wrappers import network as pvm_net
from pypowervm.wrappers import virtual_io_server as pvm_vios

from networking_powervm.plugins.ibm.agent.powervm import exceptions as np_exc

LOG = logging.getLogger(__name__)
NON_SEA_BRIDGES = ['MGMTSWITCH', 'NovaLinkVEABridge']


def parse_sea_mappings(adapter, host_uuid, mapping):
    """This method will parse the sea mappings, and return a UUID map.

    The UUID of the NetworkBridges are required for modification of the
    VLANs that are bridged through the system (via the
    SharedEthernetAdapters). However, UUIDs are not user consumable.  This
    method will read in the string from the CONF file and return a mapping
    for the physical networks.

    Input:
     - <ph_network>:<sea>:<vios_name>,<next ph_network>:<sea2>:<vios_name>
     - Example: default:ent5:vios_lpar,speedy:ent6:vios_lpar

    Output:
    {
      'default': <Network Bridge UUID>, 'speedy': <Network Bridge 2 UUID>
    }

    :param adapter: The pypowervm adapter.
    :param host_uuid: The UUID for the host system.
    :param mapping: The mapping string as defined above to parse.
    :return: The output dictionary described above.
    """
    # Read all the network bridges.
    nb_wraps = list_bridges(adapter, host_uuid)

    if len(nb_wraps) == 0:
        raise np_exc.NoNetworkBridges()
    # Did the user specify the mapping?
    if mapping == '':
        return _parse_empty_bridge_mapping(nb_wraps)

    # Need to find a list of all the VIOSes names to hrefs
    vio_wraps = pvm_vios.VIOS.get(adapter, xag=[pvm_const.XAG.VIO_NET])

    # Response dictionary
    resp = {}

    # Parse the strings
    trios = mapping.split(',')
    for trio in trios:
        # Keys
        # 0 - physical network
        # 1 - SEA name
        # 2 - VIO name
        keys = trio.split(':')

        # Find the VIOS wrapper for the name
        vio_w = next(v for v in vio_wraps if v.name == keys[2])

        # For each network bridge, see if it maps to the SEA name/VIOS href
        matching_nb = None
        for nb_wrap in nb_wraps:
            for sea in nb_wrap.seas:
                sea_vio_uuid = pvm_util.get_req_path_uuid(
                    sea.vio_uri, preserve_case=True)
                if sea.dev_name == keys[1] and sea_vio_uuid == vio_w.uuid:
                    # Found the matching SEA.
                    matching_nb = nb_wrap
                    break

        # Assuming we found a matching SEA, add it to the dictionary
        if matching_nb is not None:
            resp[keys[0]] = matching_nb.uuid
        else:
            raise np_exc.DeviceNotFound(dev=keys[1], vios=keys[2],
                                        phys_net=keys[0])

    return resp


def _parse_empty_bridge_mapping(bridges):
    """Will attempt to derive a bridge mapping if not specified.

    This method is invoked if there is no bridge mapping specified.
    If this happens, it will determine if there is a single Network Bridge
    on the system.  If so, it will assert that the default Neutron
    physical network resides on the singular Network Bridge.

    If there are multiple Network Bridges, an exception is raised.

    This does allow systems to not require the bridge mappings, but it
    is not ideal.

    :param bridges: A list of the network bridges returned via the API.
    :return: The bridge mapping, with a single physical network (default).
    :raises MultiBridgeNoMapping: Thrown if there are multiple Network
                                  Bridges on the system.
    """
    if len(bridges) > 1:
        raise np_exc.MultiBridgeNoMapping()

    LOG.warning('The bridge_mappings for the agent was not specified. There '
                'was exactly one Network Bridge on the system. Agent is '
                'assuming the default network is backed by the single Network '
                'Bridge.')
    return {'default': bridges[0].uuid}


def norm_mac(mac):
    """Normalize a MAC Address from the pypowervm format to the neutron format.

    That means that the format will be converted to lower case and will have
    colons added.

    :param mac: A pypowervm mac address.  E.g. 1234567890AB
    :returns: A mac that matches the standard neutron format.
              E.g. 12:34:56:78:90:ab
    """
    mac = mac.lower().replace(':', '')
    return ':'.join(mac[i:i + 2] for i in range(0, len(mac), 2))


def find_nb_for_cna(nb_wraps, client_adpt, vswitch_map):
    """Determines the NetworkBridge (if any) supporting a client adapter.

    :param nb_wraps: The network bridge wrappers on the system.
    :param client_adpt: The client adapter wrapper.
    :param vswitch_map: Maps the vSwitch IDs to URIs.
                        See 'get_vswitch_map'
    :return The Network Bridge wrapper that is hosting the client adapter.
            If there is not one, None is returned.
    """
    for nb_wrap in nb_wraps:
        # If the vSwitch ID doesn't match the vSwitch on the CNA...don't
        # process
        if vswitch_map.get(nb_wrap.vswitch_id) != client_adpt.vswitch_uri:
            continue

        # If the VLAN is not on the network bridge, then do not process.
        if not nb_wrap.supports_vlan(client_adpt.pvid):
            continue

        # At this point, the client adapter is supported by this network
        # bridge
        return nb_wrap

    # No valid network bridge
    return None


@pvm_retry.retry()
def get_vswitch_map(adapter, host_uuid):
    """Returns a dictionary of vSwitch IDs to their URIs.

    Ex. {'0': 'https://.../VirtualSwitch/<UUID>'}

    :param adapter: The pypowervm adapter.
    :param host_uuid: The UUID for the host system.
    """
    vswitches = pvm_net.VSwitch.get(
        adapter, parent_type=pvm_ms.System, parent_uuid=host_uuid)
    resp = {}
    for vswitch in vswitches:
        resp[vswitch.switch_id] = vswitch.related_href
    return resp


def list_vifs(adapter, vif_class, include_vios_and_mgmt=False):
    """Map of partition:[VIFs] for a specific VIF type (CNA, VNIC, etc.).

    VIOS trunk adapters are never included (even if include_vios_and_mgmt=True)

    :param adapter: The pypowervm adapter.
    :param vif_class: The pypowervm wrapper class for the VIF-ish type to be
                      retrieved (CNA, VNIC, etc.).
    :param include_vios_and_mgmt: If True, the return includes VIFs belonging
                                  to the management partition; AND non-trunk
                                  VIFs belonging to VIOS partitions.  If False,
                                  both of these types are excluded.
    :return: A map of {lpar_w: [vif_w, ...]} where each vif_w is a wrapper
             of the specified vif_class.  The vif_w list may be empty for a
             given lpar_w.
    """
    # Get the VMs to query for.
    LOG.info("Gathering Virtual Machine wrappers for a list_vifs call. "
             "Include VIOS and management: %s", include_vios_and_mgmt)

    # Find the MGMT vswitch and the Novalink I/O vswitch (if configured)
    vs_exclu = []
    vswitch_list = pvm_net.VSwitch.get(adapter,
                                       parent=pvm_ms.System.get(adapter)[0])
    for vswitch in vswitch_list:
        if vswitch.name in NON_SEA_BRIDGES:
            vs_exclu.append(vswitch.switch_id)

    # Loop through the VMs
    total_vifs = {}
    for vm_wrap in pvm_par.get_partitions(adapter, lpars=True,
                                          vioses=include_vios_and_mgmt,
                                          mgmt=include_vios_and_mgmt):
        total_vifs[vm_wrap] = _find_vifs(adapter, vif_class, vm_wrap, vs_exclu)

    return total_vifs


@pvm_retry.retry(tries=200, delay_func=lambda *a, **k: time.sleep(5),
                 test_func=lambda *a, **k: True)
def _find_vifs(adapter, vif_class, vm_wrap, vs_exclu):
    """Return the list of virtual network devices (VIFs) for a partition.

    When vif_class is CNA, this method returns the list of client network
    adapters.  But it defines what a client network adapter is different from
    the pypowervm API.  It does this because pypowervm has an odd definition.

    To the SEA agent, a Client Network Adapter is:
     - ANY pypowervm CNA that is on a LPAR (OpenStack Managed/Unmanaged or
       possibly even the mgmt LPAR)
     - All CNAs on the VIOS partition types that are NOT trunk adapters.

    This method returns those two types because it is often used with the
    heal_and_optimize workflow.  That workflow wants to know all of the VLANs
    that are in use on the system, so that it can trim out any extra VLANs
    from the VIOS type VMs trunk adapters (to reduce the broadcast domain).

    :param adapter: The pypowervm API adapter.
    :param vif_class: The pypowervm wrapper class for the VIF-ish type to be
                      retrieved (CNA, VNIC, etc.).
    :param vm_wrap: The partition wrapper (LPAR or VIOS type) whose VIFs are to
                    be retrieved.
    :param vs_exclu: A list of vswitch ids to exclude.  If a VIF is connected
                     to a vswitch on this list, it will not be returned.  This
                     list contains integer vswitch IDs such as 0, 1, 2.
    """
    try:
        vif_list = vif_class.get(
            adapter, parent=vm_wrap, helpers=_remove_log_helper(adapter))

        # This method returns all of the VIF wrappers.  It will return trunk
        # adapters on LPARs, but NOT on VIOS type partitions.  Only CNA has the
        # is_tagged_vlan_supported property; the other types can't be trunk
        # adapters (TODO(IBM) yet?), so always return them.
        return [vif for vif in vif_list if
                ((isinstance(vm_wrap, pvm_lpar.LPAR) or
                  not getattr(vif, 'is_tagged_vlan_supported', False)) and
                 getattr(vif, 'vswitch_id', None) not in vs_exclu)]
    except pvm_exc.HttpError as e:
        # If it is a 404 (not found) then just skip.
        if e.response is not None and e.response.status == 404:
            return []
        else:
            raise


def _remove_log_helper(adapter):
    # Remove the log handler from the adapter so we don't log missing VMs
    # Pulling the helpers makes a copy
    helpers = adapter.helpers
    try:
        helpers.remove(pvm_log.log_helper)
    except ValueError:
        # It's not an error if we didn't find it since we don't want it.
        pass
    return helpers


@pvm_retry.retry()
def list_bridges(adapter, host_uuid):
    """Lists NetBridge wrappers on the system.

    :param adapter: The pypowervm adapter.
    :param host_uuid: The UUID for the host system.
    """
    net_bridges = pvm_net.NetBridge.get(adapter, parent_type=pvm_ms.System,
                                        parent_uuid=host_uuid)

    if len(net_bridges) == 0:
        LOG.warning('No NetworkBridges detected on the host.')

    return net_bridges


def device_detail_valid(device_detail, mac, port_id=None):
    """Validate a return from the get_device_details API.

    :param device_detail: The detail dict returned from get_device_details
                          (or one of the list returned from
                          get_devices_details_list).
    :param mac: String MAC address issued to get_device_details, for
                logging.
    :param port_id: UUID of the neutron port.  If None, the port ID is not
                    validated.
    :return: True if the device_detail passes all checks; False otherwise.
    """
    # A device detail will always come back...even if neutron has
    # no idea what the port is.  This WILL happen for PowerVM, maybe
    # an event for the mgmt partition or the secure RMC VIF.  We can
    # detect if Neutron has full device details by simply querying for
    # the mac from the device_detail
    if not device_detail.get('mac_address'):
        LOG.debug("Ignoring VIF with MAC %(mac)s because neutron doesn't know "
                  "about it.", {'mac': mac})
        return False

    if port_id is not None:
        # If the device's id (really the port uuid) doesn't match,
        # ignore it.
        dev_pid = device_detail.get('port_id')
        if dev_pid is None or port_id != dev_pid:
            LOG.warning(
                "Ignoring port because device_details port_id doesn't match "
                "the port.\nPort: %(port)s\nDevice detail: %(device_detail)s",
                {'port': port_id, 'device_detail': device_detail})
            return False

    return True
