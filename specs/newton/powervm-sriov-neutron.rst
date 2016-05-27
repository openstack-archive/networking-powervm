..
 This work is licensed under a Creative Commons Attribution 3.0 Unported
 License.

 http://creativecommons.org/licenses/by/3.0/legalcode

=============================
Support SR-IOV VF for PowerVM
=============================

https://blueprints.launchpad.net/networking-powervm/+spec/powervm-sriov_.

.. _https://blueprints.launchpad.net/networking-powervm/+spec/powervm-sriov

This blueprint accompanies a blueprint in nova-powervm to support SR-IOV
ports in OpenStack PowerVM driver. This blueprint provides networking-powervm
specific topics. Refer to nova-powervm blueprint
(https://review.openstack.org/#/c/322203/)

Problem Description
===================
Refer to nova-powervm blueprint for a detailed problem description and
glossary. This blueprint addresses consumption of SR-IOV 'direct' port in
PowerVM. PowerVM will be making use of the 'vNIC' technology in the platform
to enable SR-IOV with Live Migration capability.

The existing mechanism driver and SR-IOV agent implementation in OpenStack
involves handing SR-IOV physical ports directly on compute node. In PowerVM
this is not applicable. PowerVM's management element is a VM(partition) on
compute host. PowerVM compute node is a management partition, which, while is
Linux, does not have the physical SR-IOV hardware attached to it.  Instead,
the hardware is owned by the hypervisor and the VFs are attached to the VM via
the hypervisor. This differs from that standard SR-IOV agent in that it can not
query the linux device tree.  Which indicates the need for a new mechanism
driver/agent.

Use Cases
---------
Refer to nova-powervm blueprint for a list of use-cases supported.
Specifically for networking-powervm, mechanism driver and agent components of
this blueprint will play a role in binding the port with nova instance with
validations.

Proposed change
===============
The changes will be made in in two areas:

1. ML2 driver: SR-IOV Mechanism driver:
A new mechanism driver will be developed that supports both 'VLAN' and 'FLAT'
network types. This mechanism driver will support 'direct' vnic type. Vif type
'pvm_sriov' will be used by this mechanism driver. The agent type supported
will be 'PowerVM SR-IOV Ethernet agent'

As designed, mechanism driver works closely with agent component on compute
node. Such an agent will provide 'bridge mappings' to mechanism driver. This
mapping will contain a list of physical network names supported by the
environment. During binding stages of the neutron port with nova instance, a
validation will be performed to ensure provider:physical_network attribute of
corresponding neutron network is valid and one of the physical network names
provided by agent in its configuration data. Mechanism driver for SR-IOV
function will be implemented under networking_powervm.plugins.ml2.drivers.
mech_pvm_sriov.PvmSRIOVMechanismDriver. This will be a subclass of
SimpleAgentMechanismDriverBase.

2. Agent:
A new SR-IOV neutron agent will be developed. This agent will support agent
type 'PowerVM SR-IOV Ethernet agent' (which links to above described mechanism
driver.) This Agent implementation will provide these three functions:

a. During its startup, will gather a list of physical network names from port
labels of all physical ports across SR-IOV adapters on compute node. This data
will be provided to mechanism driver via 'configuration' aspect of agent
status.

b. When a valid neutron port is attached to nova instance, agent will update
its status to 'Active'.

Agent will be implemented as
networking-powervm.networking_powervm.plugins.ibm.agent.powervm.sriov_agent.
SRIOVNeutronAgent which will be a subclass of BasePVMNeutronAgent. Design of
this agent will be in similar lines of SEA agent.

Alternatives
------------
None

Security impact
---------------
None

Other end user impact
---------------------
None

Performance impact
------------------
None

Deployer impact
---------------
Port labels of physical ports on SR-IOV cards will have to be updated with
physical network names. pvmctl command should be used for this purpose.

Developer impact
----------------
None

Dependencies
------------
1. An updated version of Novalink PowerVM feature
2. pypowervm library - https://github.com/powervm/pypowervm_.
.. _https://github.com/powervm/pypowervm

Implementation
==============

Assignee(s)
-----------
Eric Fried (efried)
Sridhar Venkat (svenkat)
Eric Larese (erlarese)
Esha Seth (eshaseth)

Work Items
----------
1. networking-powervm changes
SR-IOV Mechanism driver extension changes
SR-IOV agent extension changes

Testing
=======
Refer to nova-powervm blueprint.

Documentation impact
====================
Refer to nova-powervm blueprint.

References
==========
Refer to nova-powervm blueprint.

History
=======
Release Name    Description
------------    -----------
Newton          Introduced
