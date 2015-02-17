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

from neutron.i18n import _LE, _LW
from neutron.openstack.common import log as logging

from pypowervm import adapter
from pypowervm.wrappers import client_network_adapter as pvm_cna
from pypowervm.wrappers import logical_partition as pvm_lpar
from pypowervm.wrappers import managed_system as pvm_ms
from pypowervm.wrappers import network as pvm_net
from pypowervm.wrappers import virtual_io_server as pvm_vios

LOG = logging.getLogger(__name__)


class NetworkBridgeUtils(object):
    '''
    This class provides a set of methods that can be used for calling in
    to the PowerVM REST API (via the python wrapper) and parsing the results
    in such a way that can be easily consumed by the agent.

    The goal of this class is to enable the agent to be focused on 'flow' and
    this holds the implementation for the methods.
    '''

    def __init__(self, pvm_server_ip, username, password, host_mtms):
        '''
        Initializes the utility class.

        :param pvm_server_ip: The IP address of the PowerVM API server.
        :param username: The user name for API operations.
        :param password: The password for the API operations.
        :param host_mtms: The host MTMS for the system.
        '''
        session = adapter.Session(pvm_server_ip, username, password,
                                  certpath=False)
        self.adapter = adapter.Adapter(session)
        self.host_id = self._get_host_uuid(host_mtms)

    def _get_host_uuid(self, host_mtms):
        # Need to get a list of the hosts, then find the matching one
        resp = self.adapter.read(pvm_ms.MS_ROOT)
        host = pvm_ms.find_entry_by_mtms(resp, host_mtms)
        if not host:
            raise Exception("Host %s not found" % host_mtms)
        return host.uuid

    def parse_sea_mappings(self, mapping):
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

        :param mapping: The mapping string as defined above to parse.
        :return: The output dictionary described above.
        """
        # Read all the network bridges.
        nb_wraps = self.list_bridges()

        # Need to find a list of all the VIOSes names to hrefs
        vio_feed = self.adapter.read(pvm_ms.MS_ROOT, root_id=self.host_id,
                                     child_type=pvm_vios.VIO_ROOT)
        vio_wraps = pvm_vios.VirtualIOServer.load_from_response(vio_feed)

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
                    if sea.dev_name == keys[1] and sea.vio_uri == vio_w.href:
                        # Found the matching SEA.
                        matching_nb = nb_wrap
                        break

            # Assuming we found a matching SEA, add it to the dictionary
            if matching_nb is not None:
                resp[keys[0]] = matching_nb.uuid
            else:
                raise Exception(_LE('Device %(dev)s on Virtual I/O Server '
                                    '%(vios)s was not found.  Unable to set '
                                    'up physical network %(phys_net)s.') %
                                {'dev': keys[1], 'vios': keys[2],
                                 'phys_net': keys[0]})

        return resp

    def norm_mac(self, mac):
        '''
        Will return a MAC Address that is normalized to match that of the
        pypowervm API.

        That means that the format will be without colons and upper cased.

        :param mac: A mac address.  Ex. 12:34:56:78:90:ab
        :returns: A mac that matches the format of the pypowervm api.
                  Ex. 1234567890AB
        '''
        return mac.upper().replace(":", "")

    def find_client_adpt_for_mac(self, mac, client_adpts=None):
        '''
        Will return the appropriate client adapter for a given mac address.

        :param mac: The mac address of the client adapter.
        :param client_adpts: The Client Adapters.  Should be passed in for
                             performance reasons.  If not, will invoke
                             list_client_adpts.
        :returns: The Client Adapter for the mac.  If one isn't found, then
                  None will be returned.
        '''
        if not client_adpts:
            client_adpts = self.list_client_adpts()

        mac = self.norm_mac(mac)

        for client_adpt in client_adpts:
            if client_adpt.mac == mac:
                return client_adpt

        # None was found.
        return None

    def list_client_adpts(self):
        '''
        Lists all of the Client Network Adapters for the running virtual
        machines.
        '''
        vms = self._list_vm_entries()
        total_cnas = []

        for vm in vms:
            for cna_uri in vm.cna_uris:
                cna_resp = self.adapter.read_by_href(cna_uri)
                ent = pvm_cna.ClientNetworkAdapter.load_from_response(cna_resp)
                total_cnas.append(ent)

        return total_cnas

    def _list_vm_entries(self):
        '''
        Returns a List of all of the Client (non-VIOS) VMs on the system.
        Does not take into account whether or not it is managed by
        OpenStack.
        '''
        vm_feed = self.adapter.read('ManagedSystem', self.host_id,
                                    'LogicalPartition')
        vm_entries = vm_feed.feed.entries
        vms = []
        for vm_entry in vm_entries:
            vms.append(pvm_lpar.LogicalPartition(vm_entry))
        return vms

    def list_bridges(self):
        '''
        Queries for the NetworkBridges on the system.  Will return the
        wrapper objects that describe Network Bridges.
        '''
        resp = self.adapter.read('ManagedSystem', self.host_id,
                                 'NetworkBridge')
        net_bridges = pvm_net.NetworkBridge.load_from_response(resp)

        if len(net_bridges) == 0:
            LOG.warn(_LW('No NetworkBridges detected on the host.'))

        return net_bridges
