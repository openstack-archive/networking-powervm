# Copyright 2016 IBM Corp.
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

import fixtures
import mock
import testtools

from networking_powervm.plugins.ibm.agent.powervm import prov_req


class TestProvisionRequest(testtools.TestCase):
    def setUp(self):
        super(TestProvisionRequest, self).setUp()
        self.mock_time = self.useFixture(fixtures.MockPatch('time.time')).mock

    def test_init(self):
        # This actually works with nothing in the dev (it should probably fail)
        preq = prov_req.ProvisionRequest('action', {}, 'lpar')
        self.assertEqual('action', preq.action)
        self.assertIsNone(preq.mac_address)
        self.assertEqual('lpar', preq.lpar_uuid)
        self.mock_time.assert_called_once_with()
        self.assertEqual(self.mock_time.return_value, preq.created_at)

    def test_eq(self):
        """Test __eq__ and __ne__."""
        dev1 = {'mac_address': 'mac1', 'some': 'garbage'}
        dev1_2 = {'mac_address': 'mac1', 'other': 'stuff'}
        dev2 = {'mac_address': 'mac2', 'some': 'garbage'}
        # ProvisionRequests with the same mac/LPAR are equal, regardless of
        # other attributes.
        self.assertEqual(
            prov_req.ProvisionRequest('plug', dev1, 'lpar'),
            prov_req.ProvisionRequest('unplug', dev1_2, 'lpar'))
        # ProvisionRequests are not equal if LPAR UUIDs don't match
        self.assertNotEqual(
            prov_req.ProvisionRequest('plug', dev1, 'lpar1'),
            prov_req.ProvisionRequest('plug', dev1, 'lpar2'))
        # ...or if MACs don't match
        self.assertNotEqual(
            prov_req.ProvisionRequest('plug', dev1, 'lpar'),
            prov_req.ProvisionRequest('plug', dev2, 'lpar'))

    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.'
                'device_detail_valid')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.norm_mac')
    def test_for_wrappers(self, mock_mac, mock_ddv):
        def device_detail_valid(detail, mac):
            self.assertEqual(detail['mac_address'], mac)
            # Since we can't rely on the order of the outer loop, fail a
            # predictable mac.
            return mac != 'mac3'
        mock_ddv.side_effect = device_detail_valid
        mock_mac.side_effect = lambda mac: mac
        agent = mock.Mock()
        lpar1, lpar2, lpar3 = mock.Mock(), mock.Mock(), mock.Mock()
        lpar1.configure_mock(name='lpar1', uuid='uuid1')
        lpar2.configure_mock(name='lpar2', uuid='uuid2')
        lpar3.configure_mock(name='lpar3', uuid='uuid3')
        # System returns vifs with macs 1-4
        lpar_vif_map = {
            lpar1: [mock.Mock(mac='mac1'), mock.Mock(mac='mac2')],
            lpar2: [mock.Mock(mac='mac3')],
            lpar3: [mock.Mock(mac='mac4')]}
        # neutron returns devices with macs 1, 3-5.
        # I.e. compared to system, mac2 is absent, and mac5 is extraneous.
        dd1, dd3, dd4, dd5 = [{'mac_address': 'mac%d' % idx} for idx in
                              (1, 3, 4, 5)]
        agent.get_devices_details_list.return_value = [dd1, dd3, dd4, dd5]
        # Run it
        ret = prov_req.ProvisionRequest.for_wrappers(agent, lpar_vif_map, 'ac')
        # We validated 1, 3, and 4.  mac2 was not validated because it wasn't
        # in the neutron device list.  mac5 was not validated because it wasn't
        # in the system vifs.
        mock_ddv.assert_has_calls([mock.call(det, det['mac_address'])
                                   for det in (dd1, dd3, dd4)], any_order=True)
        # We created ProvisionRequests for 1 and 4 (mac3 didn't validate)
        # Sort for easier validation
        ret.sort(key=lambda preq: preq.mac_address)
        self.assertEqual('mac1', ret[0].mac_address)
        self.assertEqual('mac4', ret[1].mac_address)
        self.assertEqual('uuid1', ret[0].lpar_uuid)
        self.assertEqual('uuid3', ret[1].lpar_uuid)
        self.assertEqual('ac', ret[0].action)
        self.assertEqual('ac', ret[1].action)

    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.'
                'device_detail_valid')
    @mock.patch('pypowervm.util.get_req_path_uuid')
    def test_for_event(self, mock_uuid, mock_ddv):
        def mk_evt(action, mac, vif_type, provider='NOVA_PVM_VIF',
                   etype='CUSTOM_CLIENT_EVENT'):
            return mock.Mock(
                detail='{"action": "%s", "mac": "%s", "provider": "%s",'
                       '"type": "%s"}' % (
                           action, mac, provider, vif_type), etype=etype)
        agent = mock.Mock()
        agent.vif_type = 'pvm_sea'

        # Wrong event type
        self.assertIsNone(prov_req.ProvisionRequest.for_event(
            agent, mock.Mock(etype='bogus')))
        # Use this to prove we didn't get further
        agent.get_device_details.assert_not_called()

        # Bogus JSON
        self.assertIsNone(prov_req.ProvisionRequest.for_event(
            agent, mock.Mock(etype='CUSTOM_CLIENT_EVENT', detail=None)))
        agent.get_device_details.assert_not_called()
        self.assertIsNone(prov_req.ProvisionRequest.for_event(
            agent, mock.Mock(etype='CUSTOM_CLIENT_EVENT', detail='')))
        agent.get_device_details.assert_not_called()

        # Wrong provider
        self.assertIsNone(prov_req.ProvisionRequest.for_event(
            agent, mk_evt('action', 'mac', 'pvm_sea', provider='bogus')))
        agent.get_device_details.assert_not_called()

        # Wrong vif_type
        self.assertIsNone(prov_req.ProvisionRequest.for_event(
            agent, mk_evt('action', 'mac', 'pvm_sriov')))
        agent.get_device_details.assert_not_called()

        # Unrecognized action
        self.assertIsNone(prov_req.ProvisionRequest.for_event(
            agent, mk_evt('action', 'mac', 'pvm_sea')))
        agent.get_device_details.assert_not_called()

        # Validation failure
        mock_ddv.return_value = False
        self.assertIsNone(prov_req.ProvisionRequest.for_event(
            agent, mk_evt('plug', 'amac', 'pvm_sea')))
        agent.get_device_details.assert_called_once_with('amac')
        mock_ddv.assert_called_once_with(agent.get_device_details.return_value,
                                         'amac')
        # Prove we didn't get further
        mock_uuid.assert_not_called()

        # Good path
        agent.get_device_details.reset_mock()
        mock_ddv.reset_mock()
        mock_ddv.return_value = True
        evt = mk_evt('unplug', 'amac', 'pvm_sea')
        ret = prov_req.ProvisionRequest.for_event(agent, evt)
        agent.get_device_details.assert_called_once_with('amac')
        mock_ddv.assert_called_once_with(agent.get_device_details.return_value,
                                         'amac')
        mock_uuid.assert_called_once_with(evt.data, preserve_case=True,
                                          root=True)
        self.assertEqual('unplug', ret.action)
        self.assertEqual(agent.get_device_details.return_value, ret.rpc_device)
        self.assertEqual(mock_uuid.return_value, ret.lpar_uuid)
