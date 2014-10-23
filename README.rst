=========================
PowerVM Neutron ML2 Agent
=========================
Include the URL of your launchpad blueprint:

https://blueprints.launchpad.net/nova/+spec/example

The IBM PowerVM hypervisor provides virtualization on POWER hardware.  PowerVM
admins can see benefits in their environments by making use of OpenStack.
This will implement a ML2 compatible agent (along with a Nova driver and
Ceilometer agent defined in other blueprints) that will provide capability for
PowerVM admins to natively use OpenStack.  This agent will be tied to the
Shared Ethernet Adapter technology which is currently the typical scenario for
PowerVM network virtualization.


Problem description
===================

This blueprint will provide a ML2 compatible agent for the PowerVM hypervisor.
It will be paired to the PowerVM Nova driver that is also being proposed.

This PowerVM agent will provide support for VLAN networks across Shared
Ethernet Adapters.  It will provision the VLANs on the Virtual I/O Servers
(VIOS) to support the client workload, via the PowerVM REST API.  The Nova
component will set up the peer adapter as part of VIF plugging.

The design of this agent will attempt to match, where possible, the Open
vSwitch Agent design.  However, only networks of physical type VLAN will be
supported as part of this blueprint.  Future blueprints will be used to expand
support.


Use Cases
----------

The use cases we anticipate fulfilling are the following:

* Deploy a VLAN to the specified Virtual I/O Server (or pair of servers) as
  deploys occur.

* Remove the VLAN when the last virtual machine on that server has finished
  using the network.

* Provide a heartbeat for the agent.


Project Priority
-----------------

None


Proposed change
===============

This blueprint, along with associated blueprints in Nova and Ceilometer, plans
to bring the PowerVM hypervisor into the OpenStack community.

The ML2 plugin provides a framework that allows for different types of
platform agents (eg. Linux Bridge and Open vSwitch) to support their
respective hypervisors and networking technologies.

The proposed change plans to build upon the work that the Neutron community
has done within the ML2 plugin by building a PowerVM Neutron Agent that is ML2
compatible.

The agent will provision the necessary VLAN to support the workload on the
Virtual I/O Servers.  The scope of this initial blueprint will limit the
provider network type to VLAN.  The actions against the Virtual I/O Servers
will be done via the PowerVM REST API stack via a python wrapper.

This agent is planned to be developed in StackForge under a powervm project.
A separate blueprint will be proposed for the L release of OpenStack to
promote it to the Neutron project.  This will allow a longer period of time to
show functional testing and drive maturity.

Until the promotion to the core Neutron project is complete, this agent will
be marked experimental.


Alternatives
------------

A possible alternative would be to create a new PowerVM Neutron plugin.
However, that would not fit within broader project goals, and would end up
reimplementing logic.  It would also not allow the hypervisor to exist in a
heterogeneous environment with other hypervisor types.


Data model impact
-----------------

None


REST API impact
---------------

None


Security impact
---------------

None


Notifications impact
--------------------

None


Other end user impact
---------------------

None to the deployer.

For the Kilo release of OpenStack, the administrator will need to obtain the
agent from StackForge (and understand it’s experimental status).  The cloud
administrator will then need to configure a PowerVM specific ini file.  This
will include defining which Shared Ethernet Adapters a given physical network
maps to.


Performance Impact
------------------

It is a goal to provide a similar level of performance to the existing Open
vSwitch Agent.


Other deployer impact
---------------------

A default ini file will provide a template of information that details
configuring the agent.  Administrators will need to modify this ini file to
fit their needs.

Information that the administrator will need to provide will include:

* Mapping of the physical networks that apply to this agent.

* For each physical network, which segmentation IDs (VLANs) are supported
  against a given Shared Ethernet Adapter.


Developer impact
----------------

None


Implementation
==============

Assignee(s)
-----------

Primary assignee:
  thorst

Other contributors:
  kyleh
  dwarcher

Work Items
----------

* Create a PowerVM specific agent package in the
  /neutron/plugins/ml2/agents/powervm/ folder.  Stub out the methods.

* Create a baseline ini file that provides information needed to bring up the
  agent and map it to provider networks.

* Build in a heartbeat mechanism for the agent.

* Build a polling loop that listens for port changes.

* Determine ports added or removed.  Upon an add or remove, use the PowerVM
  REST API (via the open source python wrapper) to ensure that the appropriate
  Shared Ethernet Adapter has the necessary VLAN.

* Provide extensive unit tests (part of other work items).

* Implement a functional automation server that listens for incoming change
  set commits from the community and provides a non-gating vote (+1 or -1) on
  the change.


Dependencies
============

* The Neutron ML2 Plugin.

* Will utilize the PowerVM REST API specification for management.  Will
  utilize future versions of this specification as it becomes available:
  http://ibm.co/1lThV9R

* Will build on top of a new open source python binding to previously noted
  PowerVM REST API.  This will be a prerequisite to utilizing the driver.


Testing
=======

Tempest Tests
-------------

Since the tempest tests should be implementation agnostic, the existing
tempest tests should be able to run against the PowerVM agent without issue.
This blueprint does not foresee any changes based off this agent.

Thorough unit tests will be created with the agent to validate specific
functions within this implementation.


Functional Tests
----------------

A third party functional test environment will be created.  It will monitor
for incoming neutron change sets.  Once it detects a new change set, it will
execute the existing lifecycle API tests.  A non-gating vote (+1 or -1) will
be provided with information provided (logs) based on the result.


API Tests
---------

The REST APIs are not planned to change as part of this.  Existing APIs should
be valid.  All testing is planned within the functional testing system and via
unit tests.


Documentation Impact
====================

User Documentation
------------------

Documentation will be contributed which identifies how to set up and configure
the agent.  This will include configuring the dependencies specified above.

Documentation will be done on wiki, specifically at a minimum to the following
page: http://docs.openstack.org/icehouse/install-guide/install/yum/content/neutron-ml2-compute-node.html

Interlock will be done with the OpenStack documentation team.


Developer Documentation
-----------------------

No developer documentation additions are anticipated.  If the existing
developer documentation is updated to reflect more hypervisor specific items,
this agent will follow suit.


References
==========

* Neutron ML2 Plugin: https://wiki.openstack.org/wiki/Neutron/ML2

* PowerVM REST API Initial Specification (may require newer versions as they
  become available): http://ibm.co/1lThV9R

* PowerVM Virtualization Introduction and Configuration:
  http://www.redbooks.ibm.com/abstracts/sg247940.html

* PowerVM Best Practices: http://www.redbooks.ibm.com/abstracts/sg248062.html
