=========================
PowerVM Neutron ML2 Agent
=========================

The `IBM PowerVM hypervisor`_ provides virtualization on POWER hardware.
PowerVM operators can see benefits in their environments by making use of
OpenStack. This project implements a ML2 compatible agent that provides
capability for PowerVM admins to natively use OpenStack Neutron.  This agent is
tied to the Shared Ethernet Adapter technology which is currently the typical
scenario for PowerVM network virtualization.

.. _IBM PowerVM hypervisor: http://www.redbooks.ibm.com/abstracts/sg247940.html?Open


Problem description
===================

This project provides a ML2 compatible agent for the PowerVM hypervisor.
It is paired to the `nova-powervm`_ driver.

This PowerVM agent provides support for VLAN networks across Shared
Ethernet Adapters.  It provisions the VLANs on the Virtual I/O Servers
(VIOS) to support the client workload, via the PowerVM REST API.  The Nova
component will set up the peer adapter as part of VIF plugging.

Only networks of physical type VLAN are supported.

.. _nova-powervm: https://launchpad.net/nova-powervm


Use Cases
---------

* Deploy a VLAN to the specified Virtual I/O Server (or pair of servers) as
  deploys occur.

* Periodic heal of the systems (similar to Open vSwitch agent design).

* Periodic optimization (removal of unused VLANs from the Shared Ethernet
  Adapters) of the system.

* Heartbeat of the agent.


Project Priority
----------------

None


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

None to end user.


Performance Impact
------------------

No performance impact.  Deploy operations should not be impacted by using this
agent.


Other deployer impact
---------------------

The operator needs to obtain the agent from the code repository.  The cloud
administrator needs to install the agent on both the Neutron controller as well
as on the compute node.

The operator will then need to configure the bridge_mappings, to define in the
CONF file how to map the physical networks to the adapters.  No further
configuration is required for the operator.  If only one physical network
exists (the default), and a single Shared Ethernet Adapter, no bridge_mapping
configuration is required.  The agent will assume the default network maps to
that single Shared Ethernet Adapter (or single pair SEAs set up for redundancy).

Redundant Shared Ethernet Adapters (as defined by the `PowerVM Redbook`_) are
fully supported by this agent.

.. _PowerVM Redbook: http://www.redbooks.ibm.com/abstracts/sg247940.html

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
  wpward
  svenkat
  efried


Dependencies
============

* The Neutron ML2 Plugin.

* Utilizes the PowerVM REST API specification for management.  Will
  utilize future versions of this specification as it becomes available:
  http://ibm.co/1lThV9R

* Builds on top of the `pypowervm`_ library.  An open-source, python based
  library that interacts with the PowerVM REST API.

.. _pypowervm: https://github.com/powervm/pypowervm


Testing
=======

Tempest Tests
-------------

Since the tempest tests should be implementation agnostic, the existing
tempest tests should be able to run against the PowerVM agent without issue.

Thorough unit tests exist within the agent that validate specific functions
for this implementation.


Functional Tests
----------------

A third party functional test environment has been created.  It monitors
incoming Neutron change sets.  Once it detects a new change set, it should
execute the existing lifecycle API tests.  A non-gating vote (+1 or -1) will
be provided with information provided (logs) based on the result.

Work continues in this area.


API Tests
---------

No changes (no new APIs)



References
==========

* Neutron ML2 Plugin: https://wiki.openstack.org/wiki/Neutron/ML2

* PowerVM REST API Initial Specification (may require newer versions as they
  become available): http://ibm.co/1lThV9R

* PowerVM Virtualization Introduction and Configuration:
  http://www.redbooks.ibm.com/abstracts/sg247940.html

* PowerVM Best Practices: http://www.redbooks.ibm.com/abstracts/sg248062.html
