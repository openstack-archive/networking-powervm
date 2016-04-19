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

import mock

from oslo_config import cfg
from pypowervm.helpers import log_helper as log_hlp
from pypowervm.helpers import vios_busy as vio_hlp
from pypowervm.tests import test_fixtures as pvm_fx

from networking_powervm.plugins.ibm.agent.powervm import agent_base
from networking_powervm.tests.unit.plugins.ibm.powervm import base


class FakeExc(Exception):
    pass


def FakeNPort(mac, segment_id, phys_network):
    return {'mac_address': mac, 'segmentation_id': segment_id,
            'physical_network': phys_network}


class TestAgentBaseInit(base.BasePVMTestCase):
    """A test class to validate the set up of the agent with the API.

    This is typically mocked out in fixtures otherwise.
    """

    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.agent_base.'
                'utils.get_host_uuid')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.agent_base.'
                'CNAEventHandler')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.agent_base.'
                'BasePVMNeutronAgent.setup_rpc')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.agent_base.'
                'BasePVMNeutronAgent.parse_bridge_mappings')
    @mock.patch('pypowervm.adapter.Adapter')
    @mock.patch('pypowervm.adapter.Session')
    def test_setup_adapter(self, mock_session, mock_adapter,
                           mock_parse_mappings, mock_setup_rpc,
                           mock_evt_handler, mock_host_uuid):
        # Set up the mocks.
        mock_evt_listener = (mock_session.return_value.get_event_listener.
                             return_value)
        mock_evt_handler.return_value = 'evt_hdlr'
        mock_host_uuid.return_value = 'host_uuid'

        # Setup and invoke
        neut_agt = agent_base.BasePVMNeutronAgent('bin', 'type')

        # Validate
        mock_session.assert_called_once_with(conn_tries=300)
        mock_adapter.assert_called_once_with(
            mock_session.return_value,
            helpers=[log_hlp.log_helper, vio_hlp.vios_busy_retry_helper])
        self.assertEqual('host_uuid', neut_agt.host_uuid)
        mock_evt_listener.subscribe.assert_called_once_with('evt_hdlr')


class TestAgentBase(base.BasePVMTestCase):

    def build_test_agent(self):
        """Builds a simple test agent."""
        self.adpt = self.useFixture(
            pvm_fx.AdapterFx(traits=pvm_fx.LocalPVMTraits)).adpt

        with mock.patch('networking_powervm.plugins.ibm.agent.powervm.'
                        'agent_base.BasePVMNeutronAgent.setup_adapter'),\
                mock.patch('networking_powervm.plugins.ibm.agent.powervm.'
                           'agent_base.BasePVMNeutronAgent.setup_rpc'),\
                mock.patch('networking_powervm.plugins.ibm.agent.powervm.'
                           'agent_base.BasePVMNeutronAgent.'
                           'parse_bridge_mappings'):
            agent = agent_base.BasePVMNeutronAgent('binary_name', 'agent_type')
            agent.context = mock.Mock()
            agent.host_uuid = 'host_uuid'
            agent.agent_id = 'pvm'
            agent.plugin_rpc = mock.MagicMock()
            agent.adapter = self.adpt
        return agent

    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.agent_base.'
                'BasePVMNeutronAgent.get_devices_details_list')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.'
                'list_cnas')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.'
                'list_lpar_uuids')
    def test_build_system_prov_requests(self, mock_lpar_list, mock_list_cnas,
                                        mock_dev_details):
        agent = self.build_test_agent()

        # Mock data
        mock_lpar_list.return_value = ['uuid1', 'uuid2', 'uuid3']
        mock_list_cnas.side_effect = [[mock.Mock(mac='aabbccddeefd')],
                                      [mock.Mock(mac='aabbccddeefe')],
                                      [mock.Mock(mac='aabbccddeeff')]]
        mock_dev_details.return_value = [
            {'device': 'aa:bb:cc:dd:ee:fd'}, {'mac_address': 'aabbccddeefe'},
            {'mac_address': 'aa:bb:cc:dd:ee:ff'}]

        # Run the method
        prov_reqs, lpar_uuids, cnas = agent._build_system_prov_requests()

        # Validation
        self.assertEqual(2, len(prov_reqs))
        self.assertEqual(['uuid1', 'uuid2', 'uuid3'], lpar_uuids)
        self.assertEqual(3, len(cnas))

    def test_find_dev(self):
        agent = self.build_test_agent()

        mock_cna = mock.Mock(mac='aa:bb:cc:dd:ee:ff')

        # This is what a device looks like if you call neutron for a port
        # that exists on the system, but not in Neutron itself.
        mock_dev1 = {'device': 'aa:bb:cc:dd:ee:ff'}

        # This is a subset of the real data you get back for a proper port.
        mock_dev2 = {'mac_address': 'aabbccddeeff'}

        self.assertEqual(mock_dev2,
                         agent._find_dev(mock_cna, [mock_dev1, mock_dev2]))

    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.agent_base.'
                'BasePVMNeutronAgent.provision_devices')
    def test_attempt_provision(self, mock_provision):
        """Tests a successful 'attempt_provision' invocation."""
        agent = self.build_test_agent()
        provision_reqs = [mock.Mock(rpc_device='a', mac_address='a'),
                          mock.Mock(rpc_device='b', mac_address='b'),
                          mock.Mock(rpc_device='c', mac_address='c')]

        # Invoke the test method.
        agent.attempt_provision(provision_reqs)

        # Validate the provision was invoked.
        mock_provision.assert_called_with(provision_reqs)

    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.agent_base.'
                'BasePVMNeutronAgent.update_device_down')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.agent_base.'
                'BasePVMNeutronAgent.provision_devices')
    def test_attempt_provision_failure(self, mock_provision, mock_dev_down):
        """Tests a failed 'attempt_provision' invocation."""
        agent = self.build_test_agent()

        devs = [mock.Mock(), mock.Mock(), mock.Mock()]
        agent.plugin_rpc.get_devices_details_list.return_value = devs

        # Trigger some failure
        mock_provision.side_effect = FakeExc()
        provision_reqs = [mock.Mock(rpc_device='a', mac_address='a'),
                          mock.Mock(rpc_device='b', mac_address='b'),
                          mock.Mock(rpc_device='c', mac_address='c')]

        # Invoke the test method.
        self.assertRaises(FakeExc, agent.attempt_provision, provision_reqs)

        # Validate the provision was invoked, but failed.
        mock_provision.assert_called_with(provision_reqs)
        self.assertEqual(3, mock_dev_down.call_count)

    @mock.patch('pypowervm.utils.uuid.convert_uuid_to_pvm')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.agent_base.'
                'BasePVMNeutronAgent._list_updated_ports')
    def test_build_prov_requests_from_neutron(self, mock_list_uports,
                                              mock_pvid_convert):
        # Do a base check
        agent = self.build_test_agent()
        mock_list_uports.return_value = []
        self.assertEqual([], agent.build_prov_requests_from_neutron())

        cfg.CONF.set_override('host', 'fake_host')

        def build_port(pid, use_good_host=True):
            if use_good_host:
                return {'id': pid, 'binding:host_id': 'fake_host'}
            else:
                return {'id': pid, 'binding:host_id': 'bad_fake_host'}

        # Only 2 should be created
        mock_list_uports.return_value = [build_port('1'), {}, build_port('2'),
                                         build_port('4', use_good_host=False)]
        devs = [{'port_id': '3'}, {}, {'port_id': '1'}, {'port_id': '2'}]
        agent.plugin_rpc.get_devices_details_list.return_value = devs

        resp = agent.build_prov_requests_from_neutron()
        self.assertEqual(2, len(resp))


class TestProvisionRequest(base.BasePVMTestCase):

    def build_dev(self, segmentation_id, mac):
        return {'segmentation_id': segmentation_id, 'mac_address': mac,
                'physical_network': 'default', 'device_owner': 'nova:compute'}

    def build_preq(self, segmentation_id, mac):
        return agent_base.ProvisionRequest(
            self.build_dev(segmentation_id, mac), '1')

    def test_duplicate_removal(self):
        reqs = [self.build_preq(1, 'a'), self.build_preq(1, 'a'),
                self.build_preq(1, 'b'), self.build_preq(1, 'b'),
                self.build_preq(2, 'c'), self.build_preq(3, 'd')]
        reqs = list(set(reqs))

        # Make sure that the list is properly reduced.
        self.assertEqual(4, len(reqs))
        expected = [self.build_preq(1, 'a'), self.build_preq(1, 'b'),
                    self.build_preq(2, 'c'), self.build_preq(3, 'd')]
        for needle in expected:
            self.assertIn(needle, reqs)


class TestCNAEventHandler(base.BasePVMTestCase):
    """Validates that the CNAEventHandler can be invoked properly."""

    def setUp(self):
        super(TestCNAEventHandler, self).setUp()

        self.mock_agent = mock.MagicMock()
        self.handler = agent_base.CNAEventHandler(self.mock_agent)

    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.agent_base.'
                'CNAEventHandler._prov_reqs_for_uri')
    def test_process(self, mock_prov):
        events = {'URI1': 'add', 'URI2': 'delete', 'URI3': 'invalidate'}
        self.handler.process(events)

        # URI2 shouldn't be invoked.
        self.assertEqual(2, mock_prov.call_count)
        mock_prov.assert_any_call('URI1')
        mock_prov.assert_any_call('URI3')

    def test_prov_reqs_for_uri_not_lpar(self):
        """Ensures that anything but a LogicalPartition returns empty."""
        vio_uri = ('https://9.1.2.3:12443/rest/api/uom/ManagedSystem/'
                   'c5d782c7-44e4-3086-ad15-b16fb039d63b/VirtualIOServer/'
                   '3443DB77-AED1-47ED-9AA5-3DB9C6CF7089')
        self.assertEqual([], self.handler._prov_reqs_for_uri(vio_uri))

        ms_uri = ('https://9.1.2.3:12443/rest/api/uom/ManagedSystem/'
                  'c5d782c7-44e4-3086-ad15-b16fb039d63b')
        self.assertEqual([], self.handler._prov_reqs_for_uri(ms_uri))

        bad_uri = ('https://9.1.2.3')
        self.assertEqual([], self.handler._prov_reqs_for_uri(bad_uri))

    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.list_cnas')
    def test_prov_reqs_for_uri(self, mock_list_cnas):
        """Happy path testing of prov_reqs_for_uri."""
        lpar_uri = ('https://9.1.2.3:12443/rest/api/uom/ManagedSystem/'
                    'c5d782c7-44e4-3086-ad15-b16fb039d63b/LogicalPartition/'
                    '3443DB77-AED1-47ED-9AA5-3DB9C6CF7089')

        cna1 = mock.MagicMock(mac='aabbccddeeff')
        cna2 = mock.MagicMock(mac='aabbccddee11')
        mock_list_cnas.return_value = [cna1, cna2]

        resp = self.handler._prov_reqs_for_uri(lpar_uri)

        self.assertEqual(2, len(resp))
        for p_req in resp:
            self.assertIsInstance(p_req, agent_base.ProvisionRequest)

        # Called the correct macs with the CNA.
        self.mock_agent.get_device_details.assert_any_call('aa:bb:cc:dd:ee:ff')
        self.mock_agent.get_device_details.assert_any_call('aa:bb:cc:dd:ee:11')
