# Copyright 2014, 2015 IBM Corp.
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

from oslo_log import log as logging

from pypowervm import const as pvm_const
from pypowervm import exceptions as pvm_exc
from pypowervm.helpers import log_helper as pvm_log
from pypowervm import util as pvm_util
from pypowervm.utils import retry as pvm_retry
from pypowervm.wrappers import logical_partition as pvm_lpar
from pypowervm.wrappers import managed_system as pvm_ms
from pypowervm.wrappers import network as pvm_net
from pypowervm.wrappers import virtual_io_server as pvm_vios

from networking_powervm._i18n import _LW
from networking_powervm.plugins.ibm.agent.powervm import exceptions as np_exc

LOG = logging.getLogger(__name__)

"""Provides a set of utilities for API interaction and Neutron."""


def get_host_uuid(adapter):
    """Get the System wrapper and its UUID for the (single) host.

    :param adapter: The pypowervm adapter.
    """
    syswraps = pvm_ms.System.wrap(adapter.read(pvm_ms.System.schema_type))
    if len(syswraps) != 1:
        raise np_exc.MultipleHostsFound(host_count=len(syswraps))
    return syswraps[0].uuid


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
    vio_feed = adapter.read(pvm_ms.System.schema_type,
                            root_id=host_uuid,
                            child_type=pvm_vios.VIOS.schema_type,
                            xag=[pvm_const.XAG.VIO_NET])
    vio_wraps = pvm_vios.VIOS.wrap(vio_feed)

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
                if (sea.dev_name == keys[1] and
                        sea.vio_uri == vio_w.related_href):
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

    LOG.warning(_LW('The bridge_mappings for the agent was not specified.  '
                    'There was exactly one Network Bridge on the system.  '
                    'Agent is assuming the default network is backed by the '
                    'single Network Bridge.'))
    return {'default': bridges[0].uuid}


def norm_mac(mac):
    """
    Will return a MAC Address that normalizes from the pypowervm format
    to the neutron format.

    That means that the format will be converted to lower case and will
    have colons added.

    :param mac: A pypowervm mac address.  Ex. 1234567890AB
    :returns: A mac that matches the standard neutron format.
              Ex. 12:34:56:78:90:ab
    """
    mac = mac.lower().replace(':', '')
    return ':'.join(mac[i:i + 2] for i in range(0, len(mac), 2))


def find_cna_for_mac(mac, client_adpts):
    """Returns the appropriate client adapter for a given mac address.

    :param mac: The mac address of the client adapter.
    :param client_adpts: The Client Adapters from pypowervm.
    :returns: The Client Adapter for the mac.  If one isn't found, then
              None will be returned.
    """
    mac = pvm_util.sanitize_mac_for_api(mac)

    for client_adpt in client_adpts:
        if client_adpt.mac == mac:
            return client_adpt

    # None was found.
    return None


def find_nb_for_cna(nb_wraps, client_adpt, vswitch_map):
    """
    Determines the NetworkBridge (if any) that is supporting a client
    adapter.

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
    vsw_feed = adapter.read(pvm_ms.System.schema_type, root_id=host_uuid,
                            child_type=pvm_net.VSwitch.schema_type)
    vswitches = pvm_net.VSwitch.wrap(vsw_feed)
    resp = {}
    for vswitch in vswitches:
        resp[vswitch.switch_id] = vswitch.related_href
    return resp


def list_lpar_uuids(adapter, host_uuid):
    """Returns a list of all of the VM UUIDs.

    :param adapter: The pypowervm adapter.
    :param host_uuid: The UUID for the host system.
    :return: List of UUIDs for the VMs.
    """
    return [x.uuid for x in _list_vm_entries(adapter, host_uuid)]


def list_cnas(adapter, host_uuid, lpar_uuid=None):
    """Lists all of the Client Network Adapters for the running VMs.

    :param adapter: The pypowervm adapter.
    :param host_uuid: The UUID for the host system.
    :param lpar_uuid: (Optional) If specified, will only return the CNA's for
                      a given LPAR ID.
    """
    # Get the UUIDs of the VMs to query for.
    if lpar_uuid:
        vm_uuids = [lpar_uuid]
    else:
        vm_uuids = [x.uuid for x in _list_vm_entries(adapter, host_uuid)]

    # Loop through the VMs
    total_cnas = []
    for vm_uuid in vm_uuids:
        total_cnas.extend(_find_cnas(adapter, vm_uuid))

    return total_cnas


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
def _find_cnas(adapter, vm_uuid):
    try:
        # Extend the array to include the response
        vm_cna_feed_resp = adapter.read(
            pvm_lpar.LPAR.schema_type, root_id=vm_uuid,
            child_type=pvm_net.CNA.schema_type,
            helpers=_remove_log_helper(adapter))
        return pvm_net.CNA.wrap(vm_cna_feed_resp)
    except pvm_exc.HttpError as e:
        # If it is a 404 (not found) then just skip.
        if e.response is not None and e.response.status == 404:
            return []
        else:
            raise


@pvm_retry.retry()
def _list_vm_entries(adapter, host_uuid):
    """
    Returns a List of all of the Client (non-VIOS) VMs on the system.
    Does not take into account whether or not it is managed by
    OpenStack.

    :param adapter: The pypowervm adapter.
    :param host_uuid: The UUID for the host system.
    """
    vm_feed = adapter.read(pvm_ms.System.schema_type, root_id=host_uuid,
                           child_type=pvm_lpar.LPAR.schema_type)
    vm_entries = vm_feed.feed.entries
    vms = []
    for vm_entry in vm_entries:
        vms.append(pvm_lpar.LPAR.wrap(vm_entry))
    return vms


@pvm_retry.retry()
def list_bridges(adapter, host_uuid):
    """
    Queries for the NetworkBridges on the system.  Will return the
    wrapper objects that describe Network Bridges.

    :param adapter: The pypowervm adapter.
    :param host_uuid: The UUID for the host system.
    """
    resp = adapter.read(pvm_ms.System.schema_type, root_id=host_uuid,
                        child_type=pvm_net.NetBridge.schema_type)
    net_bridges = pvm_net.NetBridge.wrap(resp)

    if len(net_bridges) == 0:
        LOG.warning(_LW('No NetworkBridges detected on the host.'))

    return net_bridges


def update_cna_pvid(cna, pvid):
    """This method will update the CNA with a new PVID.

    Will handle the retry logic surrounding this.  As the CNA may have
    come from old data.

    :param cna: The CNA wrapper (client network adapter).
    :param pvid: The new pvid to put on the wrapper.
    """

    def _cna_argmod(this_try, max_tries, *args, **kwargs):
        # Refresh the CNA to get a new etag
        LOG.debug("Attempting to re-query a CNA to get latest etag.")
        cna = args[0]
        cna.refresh()
        return args, kwargs

    @pvm_retry.retry(argmod_func=_cna_argmod)
    def _func(cna, pvid):
        cna.pvid = pvid
        cna.update()

    # Run the function (w/ retry) to update the PVID
    _func(cna, pvid)
