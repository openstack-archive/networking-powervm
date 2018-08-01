# Copyright 2015, 2017 IBM Corp.
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

import abc
import time

import eventlet
from neutron.agent import rpc as agent_rpc
from neutron.conf.agent import common as a_config
from neutron_lib.agent import topics
from neutron_lib import constants as q_const
from neutron_lib import context as ctx
from oslo_config import cfg
from oslo_log import log as logging
from oslo_service import loopingcall
from pypowervm import adapter as pvm_adpt
from pypowervm.helpers import log_helper as log_hlp
from pypowervm.helpers import vios_busy as vio_hlp
from pypowervm.tasks import partition as pvm_par
from pypowervm.wrappers import event as pvm_evt
from pypowervm.wrappers import managed_system as pvm_ms

from networking_powervm._i18n import _
from networking_powervm.plugins.ibm.agent.powervm import prov_req as preq
from networking_powervm.plugins.ibm.agent.powervm import utils

eventlet.monkey_patch()


LOG = logging.getLogger(__name__)

agent_opts = [
    cfg.IntOpt('exception_interval', default=5,
               help=_("The number of seconds agent will wait between "
                      "polling when exception is caught")),
    cfg.IntOpt('heal_and_optimize_interval', default=1800,
               help=_('The number of seconds the agent should wait between '
                      'heal/optimize intervals.')),
]

cfg.CONF.register_opts(agent_opts, "AGENT")
a_config.register_agent_state_opts_helper(cfg.CONF)
a_config.register_root_helper(cfg.CONF)

ACONF = cfg.CONF.AGENT


# Event types requiring refetch of all VIFs for all LPARs.
FULL_REFETCH_EVENTS = (
    pvm_evt.EventType.CACHE_CLEARED, pvm_evt.EventType.MISSING_EVENTS,
    pvm_evt.EventType.NEW_CLIENT)
# Event types affecting a single object. The object's URI is in Event.data.
SINGLE_OBJ_EVENTS = (
    pvm_evt.EventType.INVALID_URI, pvm_evt.EventType.ADD_URI,
    pvm_evt.EventType.MODIFY_URI, pvm_evt.EventType.HIDDEN_URI,
    pvm_evt.EventType.VISIBLE_URI, pvm_evt.EventType.CUSTOM_CLIENT_EVENT,
    pvm_evt.EventType.DELETE_URI)


class PVMPluginApi(agent_rpc.PluginApi):
    pass


class VIFEventHandler(pvm_adpt.WrapperEventHandler):
    """Listens for Events from the PowerVM API that could be network events.

    This event handler will be invoked by the PowerVM API when something occurs
    on the system.  This event handler will determine if it could have been
    related to a network change.  If so, then it will add a ProvisionRequest
    to the processing queue.
    """

    def __init__(self, agent):
        self.agent = agent
        self.adapter = self.agent.adapter
        self.just_started = True

    def _refetch_all(self, prov_req_set):
        """Populate prov_req_set with ProvisionRequests for all LPARs' VIFs.

        :param prov_req_set: Set of ProvisionRequests, updated by this method.
        """
        # Create 'plug' requests for all LPARs' VIFs.  (No VIOSes or management
        # partition.)
        lpar_vifs = utils.list_vifs(self.adapter, self.agent.vif_wrapper_class)
        prov_reqs = preq.ProvisionRequest.for_wrappers(self.agent, lpar_vifs,
                                                       preq.PLUG)
        # Obliterate any 'plug' requests; they'll be replaced below if they
        # still exist.  Leave 'unplug' requests, which should represent VIFs
        # that will be absent from the comprehensive refetch result.
        rms = {req for req in prov_req_set if req.action == preq.PLUG}
        LOG.debug("Removing all existing plug requests: %s", rms)
        prov_req_set -= rms
        # Add in the wrapper-based 'plug' requests generated above.
        # They'll be ignored later if there's no matching neutron port.
        LOG.debug("Adding new wrapper-based plug requests: %s",
                  [str(prov_req) for prov_req in prov_reqs])
        prov_req_set |= set(prov_reqs)

    def _process_event(self, event, prov_req_set):
        """Process a PowerVM event, folding into prov_req_set as appropriate.

        :param event: The pypowervm.wrappers.event.Event to be processed.
        :return: True if the event resulted in an actionable ProvisionRequest
                 being added; False otherwise.
        """
        prov_req = preq.ProvisionRequest.for_event(self.agent, event)
        if prov_req is None:
            # Nothing to do
            return False

        # Consolidate requests.  Collapse if same action.  Replace if opposite
        # action.  ProvisionRequest's __eq__ will hit if the MAC and LPAR UUID
        # match; so this add will find hits for either action.
        rms = {prq for prq in prov_req_set if prq == prov_req}
        LOG.debug("Consolidating - removing requests: %s",
                  [str(rm_preq) for rm_preq in rms])
        prov_req_set -= rms
        LOG.debug("Adding new event-based request: %s", str(prov_req))
        prov_req_set.add(prov_req)

    def process(self, events):
        """Process a list of REST events.

        :param events: A list of pypowervm.wrappers.event.Event wrappers to
                       handle.
        """
        prov_req_set = set()
        do_heal = False
        for event in events:
            if event.etype in FULL_REFETCH_EVENTS:
                # On initial startup, we'll get a NEW_CLIENT event, *but*
                # heal_and_optimize will also do a full sweep; so skip this
                # that first time.
                if not self.just_started:
                    # Full refetch of all LPARs' VIFs.  Subsequent events in
                    # this iteration may still add/remove entries.
                    self._refetch_all(prov_req_set)
            elif event.etype in SINGLE_OBJ_EVENTS:
                self._process_event(event, prov_req_set)

            if self.agent.is_hao_event(event):
                LOG.info("Received heal-and-optimize event: %s", str(event))
                do_heal = True

            self.just_started = False

        # Do heal_and_optimize if requested.
        if do_heal:
            self.agent.heal_and_optimize()

        # Process any port requests accumulated above.
        self.agent.provision_devices(prov_req_set)


class BasePVMNeutronAgent(object):
    """Baseline PowerVM Neutron Agent class for extension.

    The ML2 agents have a common RPC polling framework and API callback
    mechanism.  This class provides the baseline so that other children
    classes can extend and focus on their specific functions rather than
    integration with the RPC server.
    """

    # This agent supports RPC Version 1.0.  Though agents don't boot unless
    # 1.1 or higher is specified now.
    # For reference:
    #  1.0 Initial version
    #  1.1 Support Security Group RPC
    #  1.2 Support DVR (Distributed Virtual Router) RPC
    RPC_API_VERSION = '1.1'

    @abc.abstractproperty
    def agent_id(self):
        raise NotImplementedError()

    @abc.abstractproperty
    def agent_binary_name(self):
        """Name of the executable under which the (subclass) agent runs."""
        raise NotImplementedError()

    @abc.abstractproperty
    def agent_type(self):
        raise NotImplementedError()

    @abc.abstractproperty
    def vif_type(self):
        raise NotImplementedError()

    @abc.abstractproperty
    def vif_wrapper_class(self):
        """The pypowervm wrapper class for the VIF-ish type the agent handles.

        E.g. pypowervm.wrappers.network.CNA, pypowervm.wrappers.iocard.VNIC.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def parse_bridge_mappings(self):
        """This method should return the bridge mappings dictionary.

        The pypowervm adapter will be initialized before this method is called.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def heal_and_optimize(self):
        """Ensures that the bridging supports all the needed ports.

        This method is invoked periodically (not on every RPC loop).  Its
        purpose is to ensure that the bridging supports every client VM
        properly.  If possible, it should also optimize the connections.
        """
        raise NotImplementedError()

    def is_hao_event(self, evt):
        """Determines if an Event warrants a heal_and_optimize.

        :param evt: A pypowervm.wrappers.event.Event wrapper to inspect.
        :return: True if heal_and_optimize should be invoked as a result of
                 this event; False otherwise.
        """
        return False

    def customize_agent_state(self):
        """Perform subclass-specific adjustments to self.agent_state."""
        pass

    def setup_adapter(self):
        """Configure the pypowervm adapter.

        This method assigns a valid pypowervm.adapter.Adapter to the
        self.adapter instance variable.
        """
        # Attempt multiple times in case the REST server is starting.
        self.adapter = pvm_adpt.Adapter(
            pvm_adpt.Session(conn_tries=300),
            helpers=[log_hlp.log_helper, vio_hlp.vios_busy_retry_helper])

    def __init__(self):
        """Create the PVM neutron agent. """
        self.adapter = None
        self.setup_adapter()
        self.msys = pvm_ms.System.get(self.adapter)[0]
        self.host_uuid = self.msys.uuid

        # Make sure the Virtual I/O Server(s) are available.
        pvm_par.validate_vios_ready(self.adapter)

        # Get the bridge mappings
        self.br_map = self.parse_bridge_mappings()

        self.agent_state = {
            'binary': self.agent_binary_name,
            'host': cfg.CONF.host,
            'topic': q_const.L2_AGENT_TOPIC,
            'configurations': {'bridge_mappings': self.br_map},
            'agent_type': self.agent_type,
            'start_flag': True}
        self.customize_agent_state()

        # Set Up RPC to Server
        self._setup_rpc()

        # Add VIF event handler to the session.
        evt_listener = self.adapter.session.get_event_listener()
        self._vif_event_handler = VIFEventHandler(self)
        evt_listener.subscribe(self._vif_event_handler)

    def port_update(self, context, **kwargs):
        """RPC callback indicating that a port has been updated within Neutron.

        This callback is registered as part of _setup_rpc.  It is invoked by
        the controller when a port is updated in Neutron.

        This is currently a no-op.
        """
        port = kwargs['port']
        LOG.debug('Neutron API port_update RPC received for port '
                  'id=%(port_id)s, mac=%(mac)s, instance=%(inst_id)s.',
                  {'port_id': port['id'], 'mac': port.get('mac_address'),
                   'inst_id': port['device_id']})

    def _setup_rpc(self):
        """Registers the RPC consumers for the plugin."""
        self.topic = topics.AGENT
        self.plugin_rpc = PVMPluginApi(topics.PLUGIN)
        self.state_rpc = agent_rpc.PluginReportStateAPI(topics.PLUGIN)

        self.context = ctx.get_admin_context_without_session()

        # Define the listening consumers for the agent.  ML2 only supports
        # these two update types.
        consumers = [[topics.PORT, topics.UPDATE]]

        self.connection = agent_rpc.create_consumers([self],
                                                     self.topic,
                                                     consumers)

        # Report interval is for the agent health check.
        report_interval = ACONF.report_interval
        if report_interval:
            hb = loopingcall.FixedIntervalLoopingCall(self._report_state)
            hb.start(interval=report_interval)

    def _report_state(self):
        """Reports the state of the agent back to the controller.

        Controller knows that if a response isn't provided in a certain period
        of time then the agent is dead.  This call simply tells the controller
        that the agent is alive.
        """
        # TODO(thorst) provide some level of devices connected to this agent.
        try:
            device_count = 0
            self.agent_state.get('configurations')['devices'] = device_count
            LOG.debug("Reporting agent state to neutron: %s", self.agent_state)
            self.state_rpc.report_state(self.context,
                                        self.agent_state)
            self.agent_state.pop('start_flag', None)
        except Exception:
            LOG.exception("Failed reporting state!")

    def update_device_up(self, device):
        """Calls back to neutron that a device is alive.

        :param device: The device detail from get_device[s]_details[_list].
        """
        LOG.info("Sending device up to Neutron for %s", device['device'])
        self.plugin_rpc.update_device_up(self.context, device['device'],
                                         self.agent_id, cfg.CONF.host)

    def update_device_down(self, device):
        """Calls back to neutron that a device is down.

        :param device: The device detail from get_device[s]_details[_list].
        """
        LOG.warning("Sending device DOWN to Neutron for %s", device['device'])
        self.plugin_rpc.update_device_down(self.context, device['device'],
                                           self.agent_id, cfg.CONF.host)

    def get_device_details(self, device_mac):
        """Returns a neutron device for a given mac address.

        :param device_mac: The neutron mac addresses for the device to get.
        :return: The device from neutron.
        """
        return self.plugin_rpc.get_device_details(
            self.context, utils.norm_mac(device_mac), self.agent_id,
            host=cfg.CONF.host)

    def get_devices_details_list(self, device_macs):
        """Returns list of neutron devices for a list of mac addresses.

        :param device_macs: List of neutron mac addresses for the devices to
                            get.
        :return: The list of devices from neutron.
        """
        return self.plugin_rpc.get_devices_details_list(
            self.context, [utils.norm_mac(mac) for mac in device_macs],
            self.agent_id)

    def provision_devices(self, requests):
        """Invoked when a set of new Neutron ports has been detected.

        This method should provision the bridging for the new devices.

        The subclass implementation may be non-blocking.  This means, if it
        will take a very long time to provision, or has a dependency on
        another action (ex. client VIF needs to be created), then it should
        run in a separate worker thread.

        Because of the non-blocking nature of the method, it is required that
        the child class updates the device state upon completion of the device
        provisioning.  This can be done with the agent's update_device_up/_down
        methods, or by invoking this default implementation.

        :param requests: A list of ProvisionRequest objects.
        """
        LOG.debug("Provisioning %d devices.", len(requests))
        for p_req in requests:
            if p_req.action == preq.PLUG:
                self.update_device_up(p_req.rpc_device)
            elif p_req.action == preq.UNPLUG:
                self.update_device_down(p_req.rpc_device)
            else:
                LOG.warning("Ignoring provision request with unknown action: "
                            "%s", str(p_req))

    def rpc_loop(self):
        """Periodic check for port additions/removals.

        Runs a check periodically to determine if new ports were added or
        removed.  Will call down to appropriate methods to determine correct
        course of action.
        """
        while True:
            try:
                # If the loop interval has passed, heal and optimize
                LOG.debug("Performing heal and optimization of system.")
                self.heal_and_optimize()
                time.sleep(ACONF.heal_and_optimize_interval)

            except Exception:
                LOG.exception("Error has been encountered and logged.  The "
                              "agent will retry.")
                # sleep for a while and re-loop
                time.sleep(ACONF.exception_interval)
