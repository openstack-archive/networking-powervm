========================
Installing with Devstack
========================

1. Download DevStack::

    $ git clone https://git.openstack.org/openstack-dev/devstack /opt/stack/devstack

2. Modify DevStack's local.conf to pull in this project by adding::

    [[local|localrc]]
    ...
    enable_plugin networking-powervm git.openstack.org/openstack/networking-powervm

   Example files are available in the nova-powervm project to provide
   reference on using this driver with the corresponding nova-powervm
   and ceilometer-powervm drivers. Following these example files will enable
   the appropriate drivers and services for each node type. Example config
   files for all-in-one, compute, and control nodes `can be found here. <https://github.com/openstack/nova-powervm/tree/master/devstack>`_

3. In DevStack's local.conf on any compute or AIO nodes, enable the appropriate
   networking agents by including one of the following.

   For Shared Ethernet Adapter support::
    enable_service pvm-q-sea-agt

   For SR-IOV support::
    enable_service pvm-q-sriov-agt

   For both::
    enable_service pvm-q-sea-agt pvm-q-sriov-agt

   Note that this step is NOT required on control-only nodes.

4. See networking-powervm/doc/source/devref/usage.rst, then configure the
   installation through options in local.conf as needed for your environment.
   The Q_PLUGIN_CONF_FILE (ML2) options are only needed for advanced configurations.::

    [[local|localrc]]
    ...
    Q_PLUGIN=ml2
    Q_ML2_TENANT_NETWORK_TYPE=vlan
    Q_ML2_PLUGIN_TYPE_DRIVERS=vlan

    [[post-config|/$Q_PLUGIN_CONF_FILE]]
    [agent]
    bridge_mappings = ''
    automated_powervm_vlan_cleanup = True

5. Run ``stack.sh`` from devstack::

    $ cd /opt/stack/devstack
    $ ./stack.sh
