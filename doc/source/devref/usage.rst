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
The Shared Ethernet Adapters should be set up and configured beforehand.
SR-IOV physical port labels must be set to the name of the neutron
physical network to which they are cabled.  For example, to associate
SR-IOV physical port with location code U78C9.001.WZS094N-P1-C7-T2 with
the neutron network named 'prod_net'::

  pvmctl sriov update --loc U78C9.001.WZS094N-P1-C7-T2 -s label=prod_net

Any un-labeled SR-IOV physical ports will be assumed to belong to the
'default' neutron physical network.

The operator does not need to add VLANs; those will be managed by the
networking-powervm agent directly.


Configuration File Options
--------------------------

You must identify which mechanism driver(s) neutron should use.  In the [ml2]
section of the ML2 configuration file (e.g.
``/etc/neutron/plugins/ml2/ml2_conf.ini``), the value of ``mechanism_drivers``
should be set to a comma-separated list of the desired drivers.  The drivers
provided by networking-powervm are:

- ``pvm_sea``: Shared Ethernet Adapter (SEA) mechanism driver.
- ``pvm_sriov``: SR-IOV mechanism driver for virtual NIC.

If using only ``pvm_sea`` and there is only a single Shared Ethernet Adapter (or
adapter pair) using the default physical network, no further configuration is
required (but see Optional Configuration below).

If using ``pvm_sriov``, you must inform the compute driver which physical
networks are allowed to be used by VMs.  Each SR-IOV physical port must be
labeled with its corresponding neutron network name as described in Usage above;
and each authorized network must be listed in the ``passthrough_whitelist`` in
the ``[pci]`` section of the nova configuration file (e.g.
``/etc/nova/nova.conf``).  For example, to authorize networks named ``default``
and ``prod_net``, include the following in the nova configuration file::

  [pci]
  passthrough_whitelist = [{"physical_network": "default"}, {"physical_network": "prod_net"}]


Optional Configuration
~~~~~~~~~~~~~~~~~~~~~~
The following options go in the ``[AGENT]`` section of the ML2 configuration
file.

+----------------------------------+-------+--------------------------------------------------------+
| Configuration option =           | Agent | Description                                            |
| Default Value                    |       |                                                        |
+==================================+=======+========================================================+
| bridge_mappings = ''             | SEA   | (StrOpt) The Network Bridge mappings (defined by the   |
|                                  |       | SEA) that describe how the neutron physical networks   |
|                                  |       | map to the Shared Ethernet Adapters.  This is required |
|                                  |       | if using a network other than the default; or if using |
|                                  |       | more than one SEA (or redundant SEA pair).             |
|                                  |       |                                                        |
|                                  |       | Format: <phnet1>:<sea1>:<vio1>,<phnet2>:<sea2>:<vio2>  |
|                                  |       | Example: default:ent5:vios_1,speedy:ent6:vios_1        |
+----------------------------------+-------+--------------------------------------------------------+
| automated_powervm_vlan_cleanup = | SEA   | Determines whether or not the VLANs will be removed    |
| True                             |       | from the Network Bridge if a VM is removed and it is   |
|                                  |       | the last VM on the system to use that VLAN.  By        |
|                                  |       | default, the agent will clean up VLANs to improve the  |
|                                  |       | overall system performance (by reducing broadcast      |
|                                  |       | domain).  Will only apply to VLANs not on the primary  |
|                                  |       | PowerVM virtual Ethernet adapter of the SEA.           |
+----------------------------------+-------+--------------------------------------------------------+
| vnic_required_vfs = 2            | SRIOV | (Integer) Redundancy level for the vNIC created to     |
|                                  |       | back an SR-IOV port.  The value represents the number  |
|                                  |       | of SR-IOV logical ports to create (one per physical    |
|                                  |       | port).  The binding will fail if the agent cannot find |
|                                  |       | enough physical ports with sufficient free capacity to |
|                                  |       | satisfy this setting.                                  |
+----------------------------------+-------+--------------------------------------------------------+
| vnic_vf_capacity = None          | SRIOV | (Float) Value between 0.0000 and 1.0000 indicating the |
|                                  |       | minimum guaranteed capacity of the VFs backing the     |
|                                  |       | SR-IOV vNIC.  Must be a multiple of each physical      |
|                                  |       | port's minimum capacity granularity, or the binding    |
|                                  |       | will fail.  If unspecified, the platform defaults      |
|                                  |       | the capacity for each VF to its backing physical       |
|                                  |       | port's minimum capacity granularity. [#]_              |
+----------------------------------+-------+--------------------------------------------------------+

.. [#] For more details on SR-IOV logical port capacity, see section 1.3.3 of the
       `IBM Power Systems SR-IOV Technical Overview and Introduction <https://www.redbooks.ibm.com/redpapers/pdfs/redp5065.pdf>`_.


SR-IOV-Backed Neutron Port Creation
-----------------------------------

To create a neutron port on network with ID ``$netid`` that will be serviced by
an SR-IOV vNIC::

  neutron port-create --vnic-type direct $netid

