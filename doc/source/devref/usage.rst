..
      Copyright 2015 IBM
      All Rights Reserved.

      Licensed under the Apache License, Version 2.0 (the "License"); you may
      not use this file except in compliance with the License. You may obtain
      a copy of the License at

          http://www.apache.org/licenses/LICENSE-2.0

      Unless required by applicable law or agreed to in writing, software
      distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
      WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
      License for the specific language governing permissions and limitations
      under the License.

Usage
=====

To make use of the PowerVM drivers, a PowerVM system set up with `NovaLink`_ is
required.  The networking-powervm agent should be installed on the management
VM.  That agent code also is required to be installed on the Neutron controller
as well.

.. _NovaLink: http://www-01.ibm.com/common/ssi/cgi-bin/ssialias?infotype=AN&subtype=CA&htmlfid=897/ENUS215-262&appname=USN

The NovaLink architecture is such that the network agent runs directly on the
PowerVM system.  No external management element (e.g. Hardware Management
Console or PowerVC) is needed.  Management of the virtualization is driven
through a thin virtual machine running on the PowerVM system.

Configuration of the PowerVM system and NovaLink is required ahead of time.
The Shared Ethernet Adapters should be set up and configured beforehand.  The
operator does not need to add VLANs, those will be managed by the
networking-powervm agent directly.


Configuration File Options
--------------------------

The agent has very minimal configuration required.  If there is only a single
Shared Ethernet Adapter (or adapter pair) using the default physical network,
no configuration is required.


Networking Configuration
~~~~~~~~~~~~~~~~~~~~~~~~
These configuration options go in the AGENT section of the CONF file.

+--------------------------------------+------------------------------------------------------------+
| Configuration option = Default Value | Description                                                |
+======================================+============================================================+
| bridge_mappings = ''                 | (StrOpt) The Network Bridge mappings (defined by the SEA)  |
|                                      | that describe how the neutron physical networks map to     |
|                                      | the Shared Ethernet Adapters.                              |
|                                      |                                                            |
|                                      | Format: <ph_net1>:<sea1>:<vio1>,<ph_net2>:<sea2>:<vio2>    |
|                                      | Example: default:ent5:vios_1,speedy:ent6:vios_1            |
+--------------------------------------+------------------------------------------------------------+
| pvid_update_loops = 180              | The Port VLAN ID (PVID) of the Client VM's Network         |
|                                      | Interface is updated by this agent.  There is a delay from |
|                                      | Nova between when the Neutron Port is assigned to the host,|
|                                      | and when the client VIF is created.  This variable         |
|                                      | indicates how many loops the agent should take until it    |
|                                      | determines that the port has failed to create from Nova.   |
|                                      | If no requests are in the system, the loop will wait a     |
|                                      | second before checking again.  If requests are in the      |
|                                      | system, it may take a bit longer.                          |
+--------------------------------------+------------------------------------------------------------+
| automated_powervm_vlan_cleanup =     | Determines whether or not the VLANs will be removed from   |
| True                                 | the Network Bridge if a VM is removed and it is the last   |
|                                      | VM on the system to use that VLAN.  By default, the agent  |
|                                      | will clean up VLANs to improve the overall system          |
|                                      | performance (by reducing broadcast domain).  Will only     |
|                                      | apply to VLANs not on the primary PowerVM virtual Ethernet |
|                                      | adapter of the SEA.                                        |
+--------------------------------------+------------------------------------------------------------+
