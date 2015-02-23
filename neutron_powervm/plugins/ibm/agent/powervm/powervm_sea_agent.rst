=====================================
PowerVM Shared Ethernet Adapter Agent
=====================================

Overview
========

This agent is a standard ML2 Neutron Agent that provides capability to tie into
Shared Ethernet Adapters on the PowerVM Platform.

The agent will configure the Shared Ethernet Adapter on the Virtual I/O Server
to support the workload for the VM.  This agent only supports the VLAN network
type.

Glossary
========

 - Client Network Adapter (CNA): The VEA that is created on the VM.  This is
   the device that is presented to the VM when it is booted up.  Connects to
   a corresponding VEA on the VIOS to send the traffic to the physical
   network.

 - Port VLAN ID (PVID):  Used for tagging and untagging of packets to the
   server.

 - Shared Ethernet Adapter (SEA): An adapter that acts as a 'bridge' between
   the physical adapters (or aggregation of physical adapters) and the Virtual
   Ethernet within a PowerVM system.  Resides on the Virtual I/O Servers.

   SEAs require VEA as well as an underlying system adapter (ex. physical
   Ethernet port or a Link Aggregation of physical ports).

   SEAs may have multiple VEAs attached to it.  There is a 'primary VEA' on
   a given SEA.  Additional VEAs are usually added to add many VLANs to a
   SEA.  This is required because a given VEA has a limit on the number of
   VLANs that it can support.

 - Virtual Ethernet Adapter (VEA): An adapter that sits on top of a Shared
   Ethernet Adapter (SEA) and provides the virtual networking within a PowerVM
   based system.

   VEAs require a PVID and support a set of VLANs (additional VLANs) that can
   be passed through the VEA.

 - Virtual I/O Server (VIOS): A special LPAR/Virtual Machine that contains
   physical hardware and then provides the virtualization primitives for other
   VMs.  An example of a virtualization primitive is a Virtual Ethernet
   Adapter.

 - Virtual LAN (VLAN): A 12-bit tag that is set on the Ethernet frame to
   provide isolation within a Layer 2 network.  Enables an administrator to
   have a single physical network, but segmented up into separate logical
   divisions.

Work Flow
=========

The agent determines that an action (provisioning or deprovisioning) is needed
based on port requests coming in to the agent.

The port is bound to a Neutron Network.  That network specifies a segmentation
id, a segmentation type, and physical network.

The segmentation id of the Neutron Network corresponds to the VLAN.  Neutron
supports many different segmentation types (Flat, VLAN, GRE, etc...).  If the
segmentation type of the Network is not VLAN, the port request will be ignored
and logged.

The physical network is a mapping in the INI file that will direct the action
to a given Shared Ethernet Adapter.  This essentially defines which physical
ports the traffic should go through.  Redundant Virtual I/O Servers are
supported.

Provisioning a VLAN
-------------------
VLANs will be provisioned as ports are created via the Neutron controller that
requires them.  This is typically done on a VM create.

Configuration of the VLANs will be done the following way:
 - If the VLAN is already on the Shared Ethernet Adapter (either the PVID of
   the primary Virtual Ethernet Adapter or an additional VLAN on any Virtual
   Ethernet Adapter) then no action is required.

 - If the VLAN is a PVID of an additional (non-primary) Virtual Ethernet
   Adapter attached to the Shared Ethernet Adapter, the agent will reconfigure
   to use a different, unused PVID for that additional (non-primary) Virtual
   Ethernet Adapter.  It will then add that VLAN to the Shared Ethernet
   Adapter.

 - If the VLAN is not on the Shared Ethernet Adapter:

   - It will attempt to add the VLAN to an additional (non-primary) Virtual
     Ethernet Adapter.

   - If there are no non-primary additional Virtual Ethernet Adapters, one
     will be created and attached to the Shared Ethernet Adapter.  An unused
     VLAN will be provided for the PVID of this additional Virtual Ethernet
     Adapter.  The required VLAN will be an added as an additional VLAN on the
     Virtual Ethernet Adapter.

   - If there are additional Virtual Ethernet Adapters available, the VLAN
     will attempt to add itself as an additional VLAN on the Virtual
     Ethernet Adapter.

     The adapter is chosen based on whether or not the VEA has space
     available for the additional VLAN (typically 20 VLANs per VEA).

     If there is no space on the VEA for the VLAN, a new VEA will be created
     and attached to the SEA as an additional VEA.

 - At no time will VLANs be added or removed from the primary Virtual Ethernet
   Adapter.  If the VLAN happens to already be on that adapter, either as the
   PVID or an additional VLAN, then no provisioning is needed.


Deprovisioning a VLAN
---------------------
This agent will attempt to deprovision the VLAN off of the Shared Ethernet
Adapter when the following conditions are met:

 - The VLAN is not on the primary Virtual Ethernet Adapter of the Shared
   Ethernet Adapter.

 - The VLAN is no longer needed by any virtual machines on the system.  This
   includes virtual machines that are not managed by OpenStack.


Restart of Agent
----------------
Upon the restart of the agent, the code will determine if any actions are
required to bring the system back into a state of consistency.  Consistency
will be defined by 'ports that need a VLAN provisioned'.  The deprovisioning
flow will not be considered.

This means that if any VMs that were deployed do not have their VLANs on the
virtual I/O server, those VLANs will be re-provisioned.


Considerations
==============

Prerequisites
-------------
 - The Shared Ethernet Adapter must be initially created before use.  The admin
   may back the Shared Ethernet Adapter any way they choose: single physical
   port, link aggregated EtherChannel, fail over, etc...

   - Only a single VLAN is needed when created.  The agent will provision other
     required VLANs on the SEA as requests come in from Neutron to do so.

   - It is advised to have a separate port dedicated for connectivity to the
     Virtual I/O Server, however that is not required.

 - The Virtual I/O Server must have an active RMC connection.

Capabilities
------------
 - Works with redudant Virtual I/O Servers.  Admin specifies one of the SEAs
   in the INI file.  The corresponding redundant SEA will be automatically
   determined.

Restrictions
------------
 - While the Neutron agent is tied to a specific Shared Ethernet Adapter, the
   admin should only use a single virtual switch.  This is because Nova creates
   the Client Network Adapter, and that information is not yet passed back to
   Nova.

   - Recommend using ETHERNET0.

   - Neutron does not require any updates in the INI file, but Nova will have a
     PowerVM option to map to a specific Shared Ethernet Adapter.

 - Only VLAN networks are supported with this agent.

 - The agent can only provision ~340 VLANs on a given Shared Ethernet Adapter
   due to the limits of the Shared Ethernet Adapter.

 - If the admin is using a fail over configuration, out of band operations
   (such as deleting one of the Shared Ethernet Adapters and recreating) may
   cause disruption.  Ensure that one always keeps their fail over adapters in
   sync.  When the agent provisions or deprovisions, it will be kept in sync.

 - If the RMC connection to the virtual I/O server is down, the agent will
   stop and will need to be restarted.

Required Configuration
======================
In order to operate properly, this agent requires the use of an INI file.  This
provides basic configuration to the agent.

A sample sea_agent.ini file will be provided.

General
-------
It is the goal of this agent to align with the ML2 plugin and broader OpenStack
configuration.  As such, admins are reminded to review the OpenStack guides to
configure Neutron and its L2 agent.

However, when starting this L2 agent, a config-file must be specified that
points to the sea_agent.ini.  This should be used in conjuction with the
neutron.conf (pass both files in as config-files).  The neutron.conf will tell
the agent how to talk to the Neutron controller.

Bridge Mappings
---------------
The bridge mappings provide a target for a Neutron phsyical network to point to
a Shared Ethernet Adapter.  Therefore, one could map the 'Engineering' physical
network to one Shared Ethernet Adapter, and the 'Admin' physical network to a
separate Shared Ethernet Adapter.

This is configuration required for all L2 Neutron Agents (within their
respective ini files).  However, PowerVM needs to target a physical network to
a Shared Ethernet Adapter on a given Virtual I/O Server.  Therefore the
formatting has a slight variation.

Format:
 - Multiple entries are separated with a comma

 - Each entry has the following format:
   <physical network>:<vio partition name>:<sea device>

Example:
 - bridge_mappings = engineering:vio1:ent5,admin:vio3:ent4
