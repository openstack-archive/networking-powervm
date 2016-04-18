# Copyright 2015 IBM Corp.
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

from neutron.common import exceptions

from networking_powervm._i18n import _LE


class MultipleHostsFound(exceptions.NeutronException):
    message = _LE("Expected exactly one host; found %(host_count)d.")


class NoNetworkBridges(exceptions.NeutronException):
    message = _LE('There are no network bridges (Shared Ethernet Adapters) on '
                  'the system.  Can not start the Neutron agent.')


class MultiBridgeNoMapping(exceptions.NeutronException):
    message = _LE('The system has more than one network bridge, but the '
                  'bridge_mappings have not been specified.  Please configure '
                  'the bridge_mappings before proceeding.')


class DeviceNotFound(exceptions.NeutronException):
    message = _LE('Device %(dev)s on Virtual I/O Server %(vios)s was not '
                  'found.  Unable to set up physical network %(phys_net)s.')
