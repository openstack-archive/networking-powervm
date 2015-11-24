========================
Installing with Devstack
========================

1. Download DevStack::

    $ git clone https://git.openstack.org/openstack-dev/devstack /opt/stack/devstack

2. Modify DevStack's local.conf to pull in this project by adding::

    [[local|localrc]]
    ...
    enable_plugin networking-powervm http://git.openstack.org/openstack/networking-powervm

3. See networking-powervm/doc/source/devref/usage.rst, then configure the
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
    pvid_update_loops = 180
    automated_powervm_vlan_cleanup = True

   Example devstack config files for all-in-one, compute, and control nodes `can be found here <https://github.com/openstack/nova-  powervm/tree/master/devstack>`_

4. Run ``stack.sh`` from devstack::

    $ cd /opt/stack/devstack
    $ ./stack.sh
