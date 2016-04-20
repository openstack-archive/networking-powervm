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

import copy

import eventlet
eventlet.monkey_patch()

from oslo_concurrency import lockutils
from oslo_config import cfg
from oslo_log import log as logging
from oslo_service import loopingcall

from neutron.agent.common import config as a_config
from neutron.agent import rpc as agent_rpc
from neutron.common import constants as q_const
from neutron.common import topics
from neutron import context as ctx
from pypowervm import adapter as pvm_adpt
from pypowervm.helpers import log_helper as log_hlp
from pypowervm.helpers import vios_busy as vio_hlp
from pypowervm import util as pvm_util
from pypowervm.utils import uuid as pvm_uuid

from networking_powervm._i18n import _
from networking_powervm._i18n import _LE
from networking_powervm._i18n import _LI
from networking_powervm._i18n import _LW
from networking_powervm.plugins.ibm.agent.powervm import utils

import time


LOG = logging.getLogger(__name__)


agent_opts = [
    cfg.IntOpt('exception_interval', default=5,
               help=_("The number of seconds agent will wait between "
                      "polling when exception is caught")),
    cfg.IntOpt('polling_interval', default=2,
               help=_("The number of seconds the agent will wait between "
                      "polling for local device changes.")),
    cfg.IntOpt('heal_and_optimize_interval', default=1800,
               help=_('The number of seconds the agent should wait between '
                      'heal/optimize intervals.  Should be higher than the '
                      'polling_interval as it runs in the nearest polling '
                      'loop.'))
]

cfg.CONF.register_opts(agent_opts, "AGENT")
a_config.register_agent_state_opts_helper(cfg.CONF)
a_config.register_root_helper(cfg.CONF)

ACONF = cfg.CONF.AGENT


class PVMPluginApi(agent_rpc.PluginApi):
    pass


class PVMRpcCallbacks(object):
    """
    Provides call backs (as defined in the setup_rpc method within the
    appropriate Neutron Agent class) that will be invoked upon certain
    actions from the controller.
    """

    # This agent supports RPC Version 1.0.  Though agents don't boot unless
    # 1.1 or higher is specified now.
    # For reference:
    #  1.0 Initial version
    #  1.1 Support Security Group RPC
    #  1.2 Support DVR (Distributed Virtual Router) RPC
    RPC_API_VERSION = '1.1'

    def __init__(self, agent):
        """
        Creates the call back.  Most of the call back methods will be
        delegated to the agent.

        :param agent: The owning agent to delegate the callbacks to.
        """
        super(PVMRpcCallbacks, self).__init__()
        self.agent = agent

    def port_update(self, context, **kwargs):
        port = kwargs['port']
        self.agent._update_port(port)
        LOG.debug("port_update RPC received for port: %s", port['id'])

    def network_delete(self, context, **kwargs):
        network_id = kwargs.get('network_id')
        LOG.debug("network_delete RPC received for network: %s", network_id)


class ProvisionRequest(object):
    """A request for a Neutron Port to be provisioned.

    The RPC device details provide some additional details that the port does
    not necessarily have, and vice versa.  This meshes together the required
    aspects into a single element.
    """

    def __init__(self, device_detail, lpar_uuid, client_type='vea'):
        self.segmentation_id = device_detail.get('segmentation_id')
        self.physical_network = device_detail.get('physical_network')
        self.mac_address = device_detail.get('mac_address')
        self.device_owner = device_detail.get('device_owner')
        self.rpc_device = device_detail
        self.lpar_uuid = lpar_uuid
        self.client_type = client_type

    def __eq__(self, other):
        if not isinstance(other, ProvisionRequest):
            return False

        # Really just need to check the lpar_uuid and mac.  The rest should
        # be static and identical.
        return (other.mac_address == self.mac_address and
                other.lpar_uuid == self.lpar_uuid)

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        # Mac addresses should not collide.  This should be sufficient for a
        # hash.  The equals will go just a bit further.
        return hash(self.mac_address)


class CNAEventHandler(pvm_adpt.EventHandler):
    """Listens for Events from the PowerVM API that could be network events.

    This event handler will be invoked by the PowerVM API when something occurs
    on the system.  This event handler will determine if it could have been
    related to a network change.  If so, then it will add a ProvisionRequest
    to the processing queue.
    """

    def __init__(self, agent):
        self.agent = agent
        self.adapter = self.agent.adapter
        self.host_uuid = self.agent.host_uuid
        self.prov_req_queue = []

    @lockutils.synchronized('cna_request_queue')
    def process(self, events):
        for uri, action in events.items():
            if action in ['add', 'invalidate']:
                self.prov_req_queue.extend(self._prov_reqs_for_uri(uri))

    def _prov_reqs_for_uri(self, uri):
        """Returns set of ProvisionRequests for a URI.

        When the API indicates that a URI is invalid, it will return a
        List of ProvisionRequests for a given URI.  If the URI is not valid
        for a ClientNetworkAdapter (CNA) then an empty list will be returned.
        """
        try:
            if not pvm_util.is_instance_path(uri):
                return []
        except Exception:
            LOG.warning(_LW('Unable to parse URI %s for provision request '
                            'assessment.'), uri)
            return []

        # The event queue will only return URI's for 'root like' objects.
        # This is essentially just the LogicalPartition, you can't get the
        # ClientNetworkAdapter.  So if we find an add/invalidate for the
        # LogicalPartition, we'll get all the CNAs.
        #
        # This check will throw out everything that doesn't include the
        # LogicalPartition's
        uuid = pvm_util.get_req_path_uuid(uri, preserve_case=True)
        if not uri.endswith('LogicalPartition/' + uuid):
            return []

        # For the LPAR, get the CNAs.
        cna_wraps = utils.list_cnas(self.adapter, self.host_uuid, uuid)
        resp = []
        for cna_w in cna_wraps:
            # Build a provision request for each type
            device_mac = utils.norm_mac(cna_w.mac)
            device_detail = self.agent.get_device_details(device_mac)

            # A device detail will always come back...even if neutron has
            # no idea what the port is.  This WILL happen for PowerVM, maybe
            # an event for the mgmt partition or the secure RMC VIF.  We can
            # detect if Neutron has full device details by simply querying for
            # the mac from the device_detail
            if not device_detail.get('mac_address'):
                continue

            # Must be good!
            resp.append(ProvisionRequest(device_detail, uuid))
        return resp

    @lockutils.synchronized('cna_request_queue')
    def get_queue(self):
        resp = copy.copy(self.prov_req_queue)
        self.prov_req_queue = []
        return resp


class BasePVMNeutronAgent(object):
    """Baseline PowerVM Neutron Agent class for extension.

    The ML2 agents have a common RPC polling framework and API callback
    mechanism.  This class provides the baseline so that other children
    classes can extend and focus on their specific functions rather than
    integration with the RPC server.
    """

    def __init__(self, binary_name, agent_type):
        # Create the utility class that enables work against the Hypervisors
        # Shared Ethernet NetworkBridge.
        self.setup_adapter()

        # Get the bridge mappings
        self.br_map = self.parse_bridge_mappings()

        self.agent_state = {
            'binary': binary_name,
            'host': cfg.CONF.host,
            'topic': q_const.L2_AGENT_TOPIC,
            'configurations': {'bridge_mappings': self.br_map},
            'agent_type': agent_type,
            'start_flag': True}
        # A list of ports that maintains the list of current 'modified' ports
        self.updated_ports = []

        # Set Up RPC to Server
        self.setup_rpc()

    def setup_adapter(self):
        """Configures the pypowervm adapter and utilities."""
        # Build the adapter.  May need to attempt the connection multiple times
        # in case the REST server is starting.
        session = pvm_adpt.Session(conn_tries=300)
        self.adapter = pvm_adpt.Adapter(
            session, helpers=[log_hlp.log_helper,
                              vio_hlp.vios_busy_retry_helper])
        self.host_uuid = utils.get_host_uuid(self.adapter)

        # Add an event handler to the session.
        evt_listener = session.get_event_listener()
        self._cna_event_handler = CNAEventHandler(self)
        evt_listener.subscribe(self._cna_event_handler)

    def parse_bridge_mappings(self):
        """This method should return the bridge mappings dictionary.

        The pypowervm adapter will be initialized before this method is called.
        """
        raise NotImplementedError()

    def setup_rpc(self):
        """Registers the RPC consumers for the plugin."""
        self.agent_id = 'sea-agent-%s' % cfg.CONF.host
        self.topic = topics.AGENT
        self.plugin_rpc = PVMPluginApi(topics.PLUGIN)
        self.state_rpc = agent_rpc.PluginReportStateAPI(topics.PLUGIN)

        self.context = ctx.get_admin_context_without_session()

        # Defines what will be listening for incoming events from the
        # controller.
        self.endpoints = [PVMRpcCallbacks(self)]

        # Define the listening consumers for the agent.  ML2 only supports
        # these two update types.
        consumers = [[topics.PORT, topics.UPDATE],
                     [topics.NETWORK, topics.DELETE]]

        self.connection = agent_rpc.create_consumers(self.endpoints,
                                                     self.topic,
                                                     consumers)

        # Report interval is for the agent health check.
        report_interval = cfg.CONF.AGENT.report_interval
        if report_interval:
            hb = loopingcall.FixedIntervalLoopingCall(self._report_state)
            hb.start(interval=report_interval)

    def _report_state(self):
        """
        Reports the state of the agent back to the controller.  Controller
        knows that if a response isn't provided in a certain period of time
        then the agent is dead.  This call simply tells the controller that
        the agent is alive.
        """
        # TODO(thorst) provide some level of devices connected to this agent.
        try:
            device_count = 0
            self.agent_state.get('configurations')['devices'] = device_count
            self.state_rpc.report_state(self.context,
                                        self.agent_state)
            self.agent_state.pop('start_flag', None)
        except Exception:
            LOG.exception(_LE("Failed reporting state!"))

    def update_device_up(self, device):
        """Calls back to neutron that a device is alive."""
        self.plugin_rpc.update_device_up(self.context, device['device'],
                                         self.agent_id, cfg.CONF.host)

    def update_device_down(self, device):
        """Calls back to neutron that a device is down."""
        self.plugin_rpc.update_device_down(self.context, device['device'],
                                           self.agent_id, cfg.CONF.host)

    def get_device_details(self, device_mac):
        """Returns a neutron device for a given mac address.

        :param device_mac: The neutron mac addresses for the device to get.
        :return: The device from neutron.
        """
        return self.plugin_rpc.get_device_details(self.context, device_mac,
                                                  self.agent_id)

    def get_devices_details_list(self, device_macs):
        """Returns list of neutron devices for a list of mac addresses.

        :param device_macs: List of neutron mac addresses for the devices to
                            get.
        :return: The list of devices from neutron.
        """
        return self.plugin_rpc.get_devices_details_list(
            self.context, device_macs, self.agent_id)

    def _update_port(self, port):
        """Invoked to indicate that a port has been updated within Neutron."""
        LOG.info(_LI('Neutron API indicated port update for %(mac)s.  '
                     'Checking if hosted by this system.'),
                 {'mac': port.get('mac_address')})
        self.updated_ports.append(port)

    def _list_updated_ports(self):
        """
        Will return (and then reset) the list of updated ports received
        from the system.
        """
        ports = copy.copy(self.updated_ports)
        self.updated_ports = []
        return ports

    def _build_system_prov_requests(self):
        """Builds ProvisionRequests for the entire system.

        It is expected this is used in the heal_and_optimize code path.  After
        these devices are pulled, something will need to reset their states
        back to 'up'.  That is the requirement of the implementing class.
        """
        prov_reqs = []

        # Get all the LPARs...we need to match up the client adapters with a
        # given device.
        lpar_uuids = utils.list_lpar_uuids(self.adapter, self.host_uuid)
        overall_cnas = []
        lpar_cna_map = {}
        for lpar_uuid in lpar_uuids:
            lpar_cna_map[lpar_uuid] = utils.list_cnas(
                self.adapter, self.host_uuid, lpar_uuid=lpar_uuid)
            overall_cnas.extend(lpar_cna_map[lpar_uuid])

        # Get all the devices that Neutron knows for this host.  Note that
        # we pass in all of the macs on the system.  For VMs that neutron does
        # not know about, we get back an empty structure with just the mac.
        client_macs = [utils.norm_mac(x.mac) for x in overall_cnas]
        devs = self.get_devices_details_list(client_macs)

        # Build out all of the devices that we have available to us.  Some
        # may be on the system but not part of OpenStack.  Those get ignored.
        for lpar_uuid in lpar_cna_map.keys():
            for cna in lpar_cna_map[lpar_uuid]:
                dev = self._find_dev(cna, devs)
                if dev:
                    prov_reqs.append(ProvisionRequest(dev, lpar_uuid))

        return prov_reqs, lpar_uuids, overall_cnas

    def _find_dev(self, cna, devs):
        cna_mac = utils.norm_mac(cna.mac)
        for dev in devs:
            # A device detail will always come back...even if neutron has
            # no idea what the port is.  This WILL happen for PowerVM, maybe
            # an event for the mgmt partition or the secure RMC VIF.  We can
            # detect if Neutron has full device details by simply querying for
            # the mac from the device_detail
            if not dev.get('mac_address'):
                continue

            if utils.norm_mac(dev.get('mac_address')) == cna_mac:
                return dev
        return None

    def heal_and_optimize(self, is_boot, prov_reqs, lpar_uuids, overall_cnas):
        """Ensures that the bridging supports all the needed ports.

        This method is invoked periodically (not on every RPC loop).  Its
        purpose is to ensure that the bridging supports every client VM
        properly.  If possible, it should also optimize the connections.

        :param is_boot: Indicates if this is the first call on boot up of the
                        agent.
        :param prov_reqs: A list of ProvisionRequest objects that represent
                          the Neutron ports that should exist on this system.
                          It may include ports that have already been
                          provisioned.  This method should make sure it calls
                          update_device_up/down afterwards.
        :param lpar_uuids: A list of the VM UUIDs for the REST API.
        :param overall_cnas: A list of the systems Client Network Adapters.
        """
        raise NotImplementedError()

    def provision_devices(self, requests):
        """Invoked when a set of new Neutron ports has been detected.

        This method should provision the bridging for the new devices

        Must be implemented by a subclass.

        The subclass implementation may be non-blocking.  This means, if it
        will take a very long time to provision, or has a dependency on
        another action (ex. client VIF needs to be created), then it should
        run in a separate worker thread.

        Because of the non-blocking nature of the method, it is required that
        the child class updates the device state upon completion of the device
        provisioning.  This can be done with the agent's
        update_device_up/_down methods.

        :param requests: A list of ProvisionRequest objects.
        """
        raise NotImplementedError()

    def rpc_loop(self):
        """
        Runs a check periodically to determine if new ports were added or
        removed.  Will call down to appropriate methods to determine correct
        course of action.
        """

        loop_timer = float(0)
        loop_interval = float(ACONF.heal_and_optimize_interval)
        first_loop = True

        while True:
            try:
                # If the loop interval has passed, heal and optimize
                if time.time() - loop_timer > loop_interval:
                    LOG.debug("Performing heal and optimization of system.")
                    prov_reqs, lpar_uuids, overall_cnas =\
                        self._build_system_prov_requests()
                    self.heal_and_optimize(first_loop, prov_reqs, lpar_uuids,
                                           overall_cnas)
                    first_loop = False
                    loop_timer = time.time()

                # Determine if there are new ports requested from neutron
                n_prov_reqs = self.build_prov_requests_from_neutron()

                # Get provision requests from the server
                s_prov_reqs = self.build_prov_requests_from_server()

                # Get all of the provision requests, but remove any duplicates.
                # A duplicate could occur if the server and neutron both threw
                # the same port request.
                tot_prov_reqs = n_prov_reqs + s_prov_reqs
                tot_prov_reqs = list(set(tot_prov_reqs))

                # If there are no updated ports, just sleep and re-loop
                if not tot_prov_reqs:
                    LOG.debug("No changes, sleeping %d seconds.",
                              ACONF.polling_interval)
                    time.sleep(ACONF.polling_interval)
                    continue

                # Provision the ports on the Network Bridge.
                self.attempt_provision(tot_prov_reqs)

            except Exception as e:
                LOG.exception(e)
                LOG.warning(_LW("Error has been encountered and logged.  The "
                                "agent will retry again."))
                # sleep for a while and re-loop
                time.sleep(ACONF.exception_interval)

    def build_prov_requests_from_neutron(self):
        """Builds the provisioning requests from the Neutron Server.

        The Neutron Server may have updated ports.  These port requests will
        be sent down to the agent as a ProvisionRequest.

        :return: A list of the ProvisionRequests that have come from Neutron.
        """
        # Convert the ports to devices.
        u_ports = self._list_updated_ports()
        dev_list = [x.get('mac_address') for x in u_ports]
        devices = self.get_devices_details_list(dev_list)

        # Build the network devices
        resp = []
        for port in u_ports:
            port_uuid = port.get('id')

            # Make sure we have a UUID
            if port_uuid is None:
                continue

            # Make sure the binding host matches this agent.  Otherwise it is
            # meant to provision on another agent.
            if port.get('binding:host_id') != cfg.CONF.host:
                continue

            for dev in devices:
                # If the device's id (really the port uuid) doesn't match,
                # ignore it.
                dev_pid = dev.get('port_id')
                if dev_pid is None or port_uuid != dev_pid:
                    continue

                # Valid request.  Add it
                device_id = port.get('device_id')
                lpar_uuid = pvm_uuid.convert_uuid_to_pvm(device_id).upper()
                resp.append(ProvisionRequest(dev, lpar_uuid))
        return resp

    def build_prov_requests_from_server(self):
        """Builds provisioning requests from the server.

        The server may detect that a new port has been built on its system.
        This method provides the agent implementations to detect this, and
        return a ProvisionRequest object that will be passed into the
        attempt_provision method.

        This method is not required to be implemented by agent implementations.
        """
        return self._cna_event_handler.get_queue()

    def attempt_provision(self, provision_reqs):
        """Attempts the provisioning of ports.

        This method will attempt to provision a set of ports (by wrapping
        around the provision_ports method).  If there are issues with
        provisioning the ports, this method will update the status in the
        backing Neutron server.

        :param provision_reqs: The list of ports to provision.
        """
        try:
            LOG.debug("Provisioning ports for mac addresses [ %s ]" %
                      ' '.join([x.mac_address for x in provision_reqs]))
            self.provision_devices(provision_reqs)
        except Exception:
            # Set the state of the device as 'down'
            for p_req in provision_reqs:
                self.update_device_down(p_req.rpc_device)

            # Reraise the exception
            raise
