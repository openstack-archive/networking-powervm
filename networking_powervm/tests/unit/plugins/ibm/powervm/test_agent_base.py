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

import mock

from oslo_config import cfg
from pypowervm.helpers import log_helper as log_hlp
from pypowervm.helpers import vios_busy as vio_hlp
from pypowervm.tasks import partition as pvm_par

from networking_powervm.plugins.ibm.agent.powervm import agent_base
from networking_powervm.tests.unit.plugins.ibm.powervm import base


def FakeNPort(mac, segment_id, phys_network):
    return {'mac_address': mac, 'segmentation_id': segment_id,
            'physical_network': phys_network}


class FakeAgent(agent_base.BasePVMNeutronAgent):
    def __init__(self):
        self.mock_vif_wrapper_class = mock.Mock()
        self.parse_bridge_mappings = mock.Mock()
        self.heal_and_optimize = mock.Mock()
        self.customize_agent_state = mock.Mock()
        self.setup_adapter_called = mock.Mock()
        super(FakeAgent, self).__init__()

    @property
    def agent_id(self):
        return 'agent_id'

    @property
    def agent_binary_name(self):
        return 'agent_binary_name'

    @property
    def agent_type(self):
        return 'agent_type'

    @property
    def vif_wrapper_class(self):
        return self.mock_vif_wrapper_class

    def setup_adapter(self):
        super(FakeAgent, self).setup_adapter()
        self.setup_adapter_called()


class TestAgentBaseInit(base.BasePVMTestCase):
    """A test class to validate the set up of the agent with the API.

    This is typically mocked out in fixtures otherwise.
    """
    def setUp(self):
        super(TestAgentBaseInit, self).setUp()
        self.agtfx = self.useFixture(base.AgentFx())
        # For init
        self.adpt = self.agtfx.adpt
        self.sess = self.agtfx.sess
        self.adpt.return_value.session = self.sess.return_value
        self.sysget = self.agtfx.sysget
        self.sys = self.agtfx.sys
        # For RPC
        self.plg_rpt_st_api = self.agtfx.plg_rpt_st_api
        self.gacwos = self.agtfx.gacwos
        self.crt_cons = self.agtfx.crt_cons
        self.filc = self.agtfx.filc
        # PluginAPI
        self.plug_api = self.agtfx.plug_api
        # VIFEventHandler
        self.veh = self.agtfx.veh
        # An agent to use
        self.agt = FakeAgent()

    def test_init(self):
        """Test BasePVMNeutronAgent __init__, setup_rpc, PVMPluginAPI."""
        # __init__ part 1
        self.sess.assert_called_once_with(conn_tries=300)
        self.adpt.assert_called_once_with(
            self.sess.return_value, helpers=[log_hlp.log_helper,
                                             vio_hlp.vios_busy_retry_helper])
        self.assertEqual(self.adpt.return_value, self.agt.adapter)
        # setup_adapter override was invoked
        self.agt.setup_adapter_called.assert_called_once_with()
        self.sysget.assert_called_once_with(self.adpt.return_value)
        pvm_par.validate_vios_ready.assert_called_once_with(
            self.adpt.return_value)
        self.assertEqual(self.sys, self.agt.msys)
        self.assertEqual(self.sys.uuid, self.agt.host_uuid)
        self.agt.parse_bridge_mappings.assert_called_once_with()
        self.assertEqual(self.agt.parse_bridge_mappings.return_value,
                         self.agt.br_map)
        self.assertEqual({
            'binary': 'agent_binary_name', 'host': cfg.CONF.host,
            'topic': 'N/A',
            'configurations': {'bridge_mappings': self.agt .br_map},
            'agent_type': 'agent_type', 'start_flag': True},
            self.agt.agent_state)
        self.agt.customize_agent_state.assert_called_once_with()
        # _setup_rpc
        self.assertEqual('q-agent-notifier', self.agt.topic)
        self.assertIsInstance(self.agt.plugin_rpc, agent_base.PVMPluginApi)
        self.plug_api.assert_called_once_with('q-plugin')
        self.plg_rpt_st_api.assert_called_once_with('q-plugin')
        self.assertEqual(self.plg_rpt_st_api.return_value, self.agt.state_rpc)
        self.gacwos.assert_called_once_with()
        self.assertEqual(self.gacwos.return_value, self.agt.context)
        self.crt_cons.assert_called_once_with(
            [self.agt], 'q-agent-notifier', [['port', 'update']])
        self.assertEqual(self.crt_cons.return_value, self.agt.connection)
        self.filc.assert_called_once_with(self.agt._report_state)
        self.filc.return_value.start.assert_called_once_with(interval=30.0)
        # __init__ part 2
        self.sess.return_value.get_event_listener.assert_called_once_with()
        self.veh.assert_called_once_with(self.agt)
        self.assertEqual(self.agt._vif_event_handler, self.veh.return_value)
        mock_evt_list = self.sess.return_value.get_event_listener.return_value
        mock_evt_list.subscribe.assert_called_once_with(
            self.agt._vif_event_handler)

        # No report interval => No looping call
        self.filc.reset_mock()
        self.filc.return_value.start.reset_mock()
        cfg.CONF.set_override('report_interval', 0, group='AGENT')
        FakeAgent()
        self.filc.assert_not_called()
        self.filc.return_value.start.assert_not_called()

    def test_report_state(self):
        self.agt._report_state()
        self.assertEqual(0, self.agt.agent_state['configurations']['devices'])
        self.plg_rpt_st_api.return_value.report_state.assert_called_once_with(
            self.agt.context, self.agt.agent_state)
        self.assertNotIn('start_flag', self.agt.agent_state)

    def test_update_device_up(self):
        self.agt.plugin_rpc.update_device_up = mock.Mock()
        self.agt.update_device_up({'device': 'the_device'})
        self.agt.plugin_rpc.update_device_up.assert_called_once_with(
            self.agt.context, 'the_device', self.agt.agent_id, cfg.CONF.host)

    def test_update_device_down(self):
        self.agt.plugin_rpc.update_device_down = mock.Mock()
        self.agt.update_device_down({'device': 'the_device'})
        self.agt.plugin_rpc.update_device_down.assert_called_once_with(
            self.agt.context, 'the_device', self.agt.agent_id, cfg.CONF.host)

    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.norm_mac')
    def test_get_device_details(self, mock_mac):
        """Test get_device_details and get_devices_details_list."""
        # get_device_details
        self.agt.plugin_rpc.get_device_details = mock.Mock()
        self.agt.get_device_details('mac')
        mock_mac.assert_called_once_with('mac')
        self.agt.plugin_rpc.get_device_details.assert_called_once_with(
            self.agt.context, mock_mac.return_value, self.agt.agent_id,
            host=cfg.CONF.host)
        # get_devices_details_list
        mock_mac.reset_mock()
        mock_mac.side_effect = ['mac1', 'mac2', 'mac3']
        self.agt.plugin_rpc.get_devices_details_list = mock.Mock()
        self.agt.get_devices_details_list([1, 2, 3])
        mock_mac.assert_has_calls([mock.call(val) for val in [1, 2, 3]])
        self.agt.plugin_rpc.get_devices_details_list.assert_called_once_with(
            self.agt.context, ['mac1', 'mac2', 'mac3'], self.agt.agent_id)

    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.agent_base.'
                'BasePVMNeutronAgent.update_device_up')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.agent_base.'
                'BasePVMNeutronAgent.update_device_down')
    def test_provision_devices(self, mock_down, mock_up):
        req1 = base.mk_preq('plug', 'mac1')
        req2 = base.mk_preq('unplug', 'mac2')
        req3 = base.mk_preq('plug', 'mac3')
        req4 = base.mk_preq('frobnicate', 'mac4')
        self.agt.provision_devices((req1, req2, req3, req4))
        mock_up.assert_has_calls([mock.call(req.rpc_device) for req in
                                  (req1, req3)])
        mock_down.assert_called_once_with(req2.rpc_device)

    @mock.patch('time.sleep')
    def test_rpc_loop(self, mock_sleep):
        # This is going to be a little weird.  To break out of the while True
        # loop, we'll have to have the exception path raise a new exception.
        # Set this up as follows:
        # 1) Run heal_and_optimize successfully
        # 2) Run heal_and_optimize successfully
        # 3) heal_and_optimize raises to prove the exception path
        # 4) Run heal_and_optimize successfully
        # 5) heal_and_optimize raises to allow us to bail
        self.agt.heal_and_optimize.side_effect = [
            None, None, Exception, None, Exception]
        # Hence time.sleep should raise on the fifth invocation
        mock_sleep.side_effect = [None, None, None, None, KeyboardInterrupt]
        # Run it
        self.assertRaises(KeyboardInterrupt, self.agt.rpc_loop)
        self.agt.heal_and_optimize.assert_has_calls([mock.call()] * 5)
        mock_sleep.assert_has_calls([mock.call(val) for val in
                                     (1800, 1800, 5, 1800, 5)])


class TestVIFEventHandler(base.BasePVMTestCase):
    """Validates that the VIFEventHandler can be invoked properly."""

    def setUp(self):
        super(TestVIFEventHandler, self).setUp()

        self.mock_agent = mock.MagicMock()
        self.handler = agent_base.VIFEventHandler(self.mock_agent)

    def test_init(self):
        """Proper initialization of instance attributes."""
        self.assertEqual(self.mock_agent, self.handler.agent)
        self.assertEqual(self.mock_agent.adapter, self.handler.adapter)
        self.assertTrue(self.handler.just_started)

    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.list_vifs')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.prov_req.'
                'ProvisionRequest.for_wrappers')
    def test_refetch_all(self, mock_preqs, mock_vifs):
        prov_req_set = set()
        req1 = base.mk_preq('plug', 'mac1', lpar_uuid='lpar1')
        req2 = base.mk_preq('unplug', 'mac2', lpar_uuid='lpar2')
        req3 = base.mk_preq('some_other_action', 'mac3', lpar_uuid='lpar3')
        req4 = base.mk_preq('plug', 'mac4', lpar_uuid='lpar4')

        # 1) No-op
        mock_preqs.return_value = []
        self.handler._refetch_all(prov_req_set)
        mock_vifs.assert_called_once_with(
            self.handler.adapter, self.mock_agent.vif_wrapper_class)
        mock_preqs.assert_called_once_with(
            self.mock_agent, mock_vifs.return_value, 'plug')
        self.assertEqual(set(), prov_req_set)

        # 2) Add some reqs. (IRL, no 'unplug' reqs would come in here, but this
        #    sets up for the next test.)
        mock_preqs.return_value = [req1, req2, req3]
        self.handler._refetch_all(prov_req_set)
        # All reqs were added.
        self.assertEqual(set(mock_preqs.return_value), prov_req_set)

        # 3) a) Trim plug reqs (req1 goes away; req3 stays);
        #    b) add new reqs (req4);
        #    c) consolidate (req2 appears once).
        mock_preqs.return_value = [req2, req4]
        self.handler._refetch_all(prov_req_set)
        self.assertEqual({req2, req3, req4}, prov_req_set)

    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.prov_req.'
                'ProvisionRequest.for_event')
    def test_process_event(self, mock_preq):
        req1 = base.mk_preq('plug', 'mac1', lpar_uuid='lpar1')
        req2 = base.mk_preq('unplug', 'mac2', lpar_uuid='lpar2')
        req3 = base.mk_preq('some_other_action', 'mac3', lpar_uuid='lpar3')
        # "matches" req2
        req4 = base.mk_preq('plug', 'mac2', lpar_uuid='lpar2')
        # Seed the req set
        prov_req_set = {req1, req2, req3}

        # 1) No req returned => no-op
        mock_preq.return_value = None
        self.handler._process_event('event', prov_req_set)
        mock_preq.assert_called_once_with(self.mock_agent, 'event')
        self.assertEqual({req1, req2, req3}, prov_req_set)

        # 2) Req req4 returned:
        #    a) req4 added to the set;
        #    b) "matching" reqs (req2) removed.
        mock_preq.return_value = req4
        self.handler._process_event('event', prov_req_set)
        self.assertEqual({req1, req3, req4}, prov_req_set)

    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.agent_base.'
                'VIFEventHandler._refetch_all')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.agent_base.'
                'VIFEventHandler._process_event')
    def test_process(self, mock_proc_evt, mock_ref_all):
        def _process_event(event, prs):
            prs.add('event %s' % event.etype)
        mock_proc_evt.side_effect = _process_event

        def _refetch_all(prs):
            prs.add('refetch')
        mock_ref_all.side_effect = _refetch_all

        # Refetch events
        r_evts = [mock.Mock(etype=val, detail='One,Two') for val in
                  agent_base.FULL_REFETCH_EVENTS]
        # Single object events
        s_evts = [mock.Mock(etype=val) for val in agent_base.SINGLE_OBJ_EVENTS]
        # Ignored events
        i_evts = [mock.Mock(etype=val, detail='Three') for val in
                  ('foo', 'bar')]

        # 1) No events => no action, just_started stays True
        self.assertTrue(self.handler.just_started)
        self.handler.process([])
        mock_ref_all.assert_not_called()
        mock_proc_evt.assert_not_called()
        self.mock_agent.is_hao_event.assert_not_called()
        self.mock_agent.provision_devices.assert_called_once_with(set())
        self.assertTrue(self.handler.just_started)

        self.mock_agent.provision_devices.reset_mock()

        # 2) Ignorable followed by non-ignorable:
        #    a) Processors are called;
        #    b) Non-ignorables are provisioned.
        #    c) No heal-and-optimize events.
        self.mock_agent.is_hao_event.return_value = False
        evts = [i_evts[0], r_evts[0], s_evts[0]]
        self.handler.process(evts)
        mock_ref_all.assert_called_once_with(mock.ANY)
        mock_proc_evt.assert_called_once_with(s_evts[0], mock.ANY)
        self.mock_agent.is_hao_event.assert_has_calls(
            [mock.call(evt) for evt in evts])
        self.mock_agent.heal_and_optimize.assert_not_called()
        self.mock_agent.provision_devices.assert_called_once_with(
            {'refetch', 'event INVALID_URI'})
        self.assertFalse(self.handler.just_started)

        mock_ref_all.reset_mock()
        mock_proc_evt.reset_mock()
        self.mock_agent.provision_devices.reset_mock()

        # 3) a) Cover all events;
        #    b) Non-ignorables are provisioned.
        #    c) Some heal-and-optimize events.
        evts = s_evts + i_evts + r_evts
        # Make is_hao_event return True periodically
        self.mock_agent.is_hao_event.side_effect = map(
            lambda x: False if x % 3 else True, range(len(evts)))
        self.handler.process(evts)
        mock_ref_all.assert_has_calls([mock.call(mock.ANY)] * len(r_evts))
        mock_proc_evt.assert_has_calls([mock.call(evt, mock.ANY)
                                        for evt in s_evts])
        # is_hao_event was called for every event
        self.mock_agent.is_hao_event.assert_has_calls(
            [mock.call(evt) for evt in evts])
        # heal_and_optimize was only called once - the loop accumulates
        self.mock_agent.heal_and_optimize.assert_called_once_with()
        self.mock_agent.provision_devices.assert_called_once_with(
            {'refetch'} | {'event %s' % val for val in
                           agent_base.SINGLE_OBJ_EVENTS})
