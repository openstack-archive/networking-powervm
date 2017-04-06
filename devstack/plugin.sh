#!/bin/bash
#
# plugin.sh - Devstack extras script to install and configure the neutron ml2
# agent for powervm

# This driver is enabled in override-defaults with:
#  Q_AGENT=${Q_AGENT:-pvm_sea}
#  Q_ML2_PLUGIN_MECHANISM_DRIVERS={Q_ML2_PLUGIN_MECHANISM_DRIVERS:-pvm_sea}

# The following entry points are called in this order for networking-powervm:
#
# - install_networking_powervm
# - configure_networking_powervm
# - start_networking_powervm
# - stop_networking_powervm
# - cleanup_networking_powervm

# Save trace setting
MY_XTRACE=$(set +o | grep xtrace)
set +o xtrace

# Defaults
# --------

# Set up base directories
NEUTRON_CONF_DIR=${NEUTRON_CONF_DIR:-/etc/neutron}
NEUTRON_CONF=${NEUTRON_CONF:-NEUTRON_CONF_DIR/neutron.conf}


# networking-powervm directories
NETWORKING_POWERVM_DIR=${NETWORKING_POWERVM_DIR:-${DEST}/networking-powervm}
NETWORKING_POWERVM_PLUGIN_DIR=$(readlink -f $(dirname ${BASH_SOURCE[0]}))

# Source functions
source $NETWORKING_POWERVM_PLUGIN_DIR/powervm-functions.sh

# Entry Points
# ------------

# configure_networking_powervm() - Configure the system to use networking_powervm
function configure_networking_powervm {
    iniset /$Q_PLUGIN_CONF_FILE ml2 mechanism_drivers $PVM_SEA_MECH_DRIVER,$PVM_SRIOV_MECH_DRIVER
}

# install_networking_powervm() - Install networking_powervm and necessary dependencies
function install_networking_powervm {
    # Install the networking-powervm package
    setup_develop $NETWORKING_POWERVM_DIR
}

# start_networking_powervm() - Start the networking_powervm process
function start_networking_powervm {
    # Check that NovaLink is installed and running
    check_novalink_install

    # Start the pvm_* ml2 agents, as requested
    if is_service_enabled pvm-q-sea-agt; then
        run_process pvm-q-sea-agt "$PVM_SEA_AGENT_BINARY --config-file $NEUTRON_CONF --config-file /$Q_PLUGIN_CONF_FILE"
    fi
    if is_service_enabled pvm-q-sriov-agt; then
        run_process pvm-q-sriov-agt "$PVM_SRIOV_AGENT_BINARY --config-file $NEUTRON_CONF --config-file /$Q_PLUGIN_CONF_FILE"
    fi
}

# stop_networking_powervm() - Stop the networking_powervm process
function stop_networking_powervm {
    # Stop the pvm_* ml2 agents
    stop_process pvm-q-sea-agt
    stop_process pvm-q-sriov-agt
}

# cleanup_networking_powervm() - Cleanup the networking_powervm process
function cleanup_networking_powervm {
    # This function intentionally left blank
    :
}

# Devstack Required Functions
# - These are required for devstack to stack properly
# - They are intentionally left blank here

function neutron_plugin_configure_dhcp_agent {
    :
}

function neutron_plugin_install_agent_packages {
    :
}

function neutron_plugin_configure_plugin_agent {
    :
}

function neutron_plugin_setup_interface_driver {
    :
}

function neutron_plugin_configure_l3_agent {
    :
}

function neutron_plugin_create_nova_conf {
    :
}

# Core Dispatch
# -------------
if [[ "$1" == "stack" && "$2" == "pre-install" ]]; then
    if is_service_enabled pvm-q-sea-agt || is_service_enabled pvm-q-sriov-agt; then
        # Install NovaLink if set
        if [[ "$INSTALL_NOVALINK" = "True" ]]; then
            echo_summary "Installing NovaLink"
            install_novalink
        fi
    fi
fi

if [[ "$1" == "stack" && "$2" == "install" ]]; then
    # Perform installation of networking-powervm
    echo_summary "Installing networking-powervm"
    install_networking_powervm

elif [[ "$1" == "stack" && "$2" == "post-config" ]]; then
    # Lay down configuration post install
    echo_summary "Configuring networking-powervm"
    configure_networking_powervm

elif [[ "$1" == "stack" && "$2" == "extra" ]]; then
    if is_service_enabled pvm-q-sea-agt || is_service_enabled pvm-q-sriov-agt; then
        # Initialize and start the PowerVM SEA and/or SR-IOV agent
        echo_summary "Starting networking-powervm"
        start_networking_powervm
    fi
fi

if [[ "$1" == "unstack" ]]; then
    if is_service_enabled pvm-q-sea-agt || is_service_enabled pvm-q-sriov-agt; then
        # Shut down PowerVM SEA and/or SR-IOV agent
        echo_summary "Stopping networking-powervm"
        stop_networking_powervm
    fi
fi

if [[ "$1" == "clean" ]]; then
    if is_service_enabled pvm-q-sea-agt || is_service_enabled pvm-q-sriov-agt; then
        # Remove any lingering configuration data
        # clean.sh first calls unstack.sh
        echo_summary "Cleaning up networking-powervm and associated data"
        cleanup_networking_powervm
    fi
fi

# Restore xtrace
$MY_XTRACE

# Local variables:
# mode: shell-script
# End:
