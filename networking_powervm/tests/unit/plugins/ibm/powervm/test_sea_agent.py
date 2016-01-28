# Copyright 2014, 2015 IBM Corp.
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

from oslo_config import cfg

import mock

from networking_powervm.plugins.ibm.agent.powervm import agent_base
from networking_powervm.plugins.ibm.agent.powervm import sea_agent
from networking_powervm.tests.unit.plugins.ibm.powervm import base
from pypowervm.tests import test_fixtures as pvm_fx

from neutron.common import constants as q_const
from neutron import context as ctx


def FakeClientAdpt(mac, pvid, tagged_vlans):
    return mock.MagicMock(mac=mac, pvid=pvid, tagged_vlans=tagged_vlans)


def FakeNPort(mac, segment_id, phys_network):
    device = {'physical_network': phys_network, 'segmentation_id': segment_id}
    return mock.Mock(mac=mac, segmentation_id=segment_id, rpc_device=device,
                     physical_network=phys_network, lpar_uuid='lpar_uuid')


def FakeNB(uuid, pvid, tagged_vlans, addl_vlans):
    m = mock.MagicMock()
    m.uuid = uuid

    lg = mock.MagicMock()
    lg.pvid = pvid
    lg.tagged_vlans = tagged_vlans

    vlans = [pvid]
    vlans.extend(tagged_vlans)
    vlans.extend(addl_vlans)
    m.list_vlans.return_value = vlans

    m.load_grps = [lg]
    return m


class FakeException(Exception):
    """Used to indicate an error in an API the agent calls."""
    pass


class SEAAgentTest(base.BasePVMTestCase):

    def setUp(self):
        super(SEAAgentTest, self).setUp()

        self.adpt = self.useFixture(
            pvm_fx.AdapterFx(traits=pvm_fx.LocalPVMTraits)).adpt

        with mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.'
                        'get_host_uuid'),\
                mock.patch('networking_powervm.plugins.ibm.agent.'
                           'powervm.utils.parse_sea_mappings') as mappings:
            mappings.return_value = {'default': 'nb_uuid'}
            self.agent = sea_agent.SharedEthernetNeutronAgent()
            self.agent.adapter = self.adpt

    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.'
                'parse_sea_mappings')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.'
                'get_host_uuid')
    def test_init(self, mock_get_host_uuid, mock_parse_mapping):
        """Verifies the integrity of the agent after being initialized."""
        mock_get_host_uuid.return_value = 'host_uuid'
        temp_agent = sea_agent.SharedEthernetNeutronAgent()
        self.assertEqual('networking-powervm-sharedethernet-agent',
                         temp_agent.agent_state.get('binary'))
        self.assertEqual(q_const.L2_AGENT_TOPIC,
                         temp_agent.agent_state.get('topic'))
        self.assertEqual(True, temp_agent.agent_state.get('start_flag'))
        self.assertEqual('PowerVM Shared Ethernet agent',
                         temp_agent.agent_state.get('agent_type'))

    def test_updated_ports(self):
        """
        Validates that the updated ports list can be added to and reset
        properly as needed.
        """
        self.assertEqual(0, len(self.agent._list_updated_ports()))

        self.agent._update_port({'mac_address': 'aa'})
        self.agent._update_port({'mac_address': 'bb'})

        self.assertEqual(2, len(self.agent._list_updated_ports()))

        # This should now be reset back to zero length
        self.assertEqual(0, len(self.agent._list_updated_ports()))

    def test_report_state(self):
        """"Validates that the report state functions properly."""
        # Make sure we had a start flag before the first report
        self.assertIsNotNone(self.agent.agent_state.get('start_flag'))

        # Mock up the state_rpc
        self.agent.state_rpc = mock.Mock()
        self.agent.context = mock.Mock()

        # run the code
        self.agent._report_state()

        # Devices are not set
        configs = self.agent.agent_state.get('configurations')
        self.assertEqual(0, configs['devices'])

        # Make sure we flipped to None after the report.  Also
        # indicates that we hit the last part of the method and didn't
        # fail.
        self.assertIsNone(self.agent.agent_state.get('start_flag'))

    @mock.patch('pypowervm.tasks.network_bridger.ensure_vlans_on_nb')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils')
    def test_provision_devices(self, mock_utils, mock_ensure):
        """Validates that the provision is invoked with batched VLANs."""
        self.agent.api_utils = mock_utils
        self.agent.br_map = {'default': 'nb_uuid'}
        self.agent.pvid_updater = mock.MagicMock()

        # Invoke
        self.agent.provision_devices([FakeNPort('aa', 20, 'default'),
                                      FakeNPort('bb', 22, 'default')])

        # Validate that both VLANs are in one call
        mock_ensure.assert_called_once_with(mock.ANY, mock.ANY, 'nb_uuid',
                                            {20, 22})

        # Validate that the PVID updates were completed
        self.assertEqual(2, self.agent.pvid_updater.add.call_count)

    @mock.patch('pypowervm.tasks.network_bridger.ensure_vlans_on_nb')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils')
    def test_provision_devices_fails(self, mock_utils, mock_ensure):
        """Validates that behavior of a failed VLAN provision."""
        self.agent.api_utils = mock_utils
        self.agent.br_map = {'default': 'nb_uuid'}
        self.agent.pvid_updater = mock.MagicMock()

        # Have the ensure throw some exception
        mock_ensure.side_effect = FakeException()

        # Invoke
        self.assertRaises(FakeException, self.agent.provision_devices,
                          [FakeNPort('aa', 20, 'default'),
                           FakeNPort('bb', 22, 'default')])

        # Validate that both VLANs are in one call.  Should still occur even
        # though no exception.
        mock_ensure.assert_called_once_with(mock.ANY, mock.ANY, 'nb_uuid',
                                            {20, 22})

        # However, the pvid updater should not be invoked.
        self.assertEqual(0, self.agent.pvid_updater.add.call_count)

    @mock.patch('pypowervm.tasks.network_bridger.remove_vlan_from_nb')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.sea_agent.'
                'SharedEthernetNeutronAgent._get_nb_and_vlan')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.sea_agent.'
                'SharedEthernetNeutronAgent.provision_devices')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.'
                'get_vswitch_map')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.list_cnas')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.'
                'list_bridges')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.'
                'find_nb_for_cna')
    def test_heal_and_optimize(
            self, mock_find_nb_for_cna, mock_list_bridges, mock_list_cnas,
            mock_vs_map, mock_prov_devs, mock_get_nb_and_vlan,
            mock_nbr_remove):
        """Validates the heal and optimization code."""
        # Fake adapters already on system.
        adpts = [FakeClientAdpt('00', 30, []),
                 FakeClientAdpt('11', 31, [32, 33, 34])]
        mock_list_cnas.return_value = adpts

        # The neutron data.  These will be 'ensured' on the bridge.
        self.agent.plugin_rpc = mock.MagicMock()
        self.agent.plugin_rpc.get_devices_details_list.return_value = [
            FakeNPort('00', 20, 'default'), FakeNPort('22', 22, 'default')]

        self.agent.br_map = {'default': 'nb_uuid'}

        # State that there is a pending VLAN (47) that has yet to be applied
        self.agent.pvid_updater = mock.MagicMock()
        self.agent.pvid_updater.pending_vlans = {47}

        # Mock a provision request
        p_req = mock.Mock()
        mock_get_nb_and_vlan.return_value = ('nb2_uuid', 23)

        # Mock up network bridges.  VLANs 44, 45, and 46 should be deleted
        # as they are not required by anything.  VLAN 47 should be needed
        # as it is in the pending list.
        mock_nb1 = FakeNB('nb_uuid', 20, [], [])
        mock_nb2 = FakeNB('nb2_uuid', 40, [41, 42, 43], [44, 45, 46, 47])
        mock_list_bridges.return_value = [mock_nb1, mock_nb2]
        mock_find_nb_for_cna.return_value = mock_nb2

        # Invoke
        self.agent.heal_and_optimize(False, [p_req], [], [])

        # Verify.  One ensure call per net bridge.
        self.assertEqual(3, mock_nbr_remove.call_count)
        mock_prov_devs.assert_called_with([p_req])

    @mock.patch('pypowervm.tasks.network_bridger.remove_vlan_from_nb')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.sea_agent.'
                'SharedEthernetNeutronAgent.provision_devices')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.'
                'get_vswitch_map')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.list_cnas')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.'
                'list_bridges')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.'
                'find_nb_for_cna')
    def test_heal_and_optimize_no_remove(
            self, mock_find_nb_for_cna, mock_list_bridges, mock_list_cnas,
            mock_vs_map, mock_prov_devs, mock_nbr_remove):
        """Validates the heal and optimization code. No remove."""
        # Fake adapters already on system.
        adpts = [FakeClientAdpt('00', 30, []),
                 FakeClientAdpt('11', 31, [32, 33, 34])]
        mock_list_cnas.return_value = adpts

        # The neutron data.  These will be 'ensured' on the bridge.
        self.agent.plugin_rpc = mock.MagicMock()
        self.agent.plugin_rpc.get_devices_details_list.return_value = [
            FakeNPort('00', 20, 'default'), FakeNPort('22', 22, 'default')]

        self.agent.br_map = {'default': 'nb_uuid'}

        # State that there is a pending VLAN (47) that has yet to be applied
        self.agent.pvid_updater = mock.MagicMock()
        self.agent.pvid_updater.pending_vlans = {47}

        # Mock up network bridges.  VLANs 44, 45, and 46 should be deleted
        # as they are not required by anything.  VLAN 47 should be needed
        # as it is in the pending list.
        mock_nb1 = FakeNB('nb_uuid', 20, [], [])
        mock_nb2 = FakeNB('nb2_uuid', 40, [41, 42, 43], [44, 45, 46, 47])
        mock_list_bridges.return_value = [mock_nb1, mock_nb2]
        mock_find_nb_for_cna.return_value = mock_nb2

        # Set that we can't do the clean up
        cfg.CONF.set_override('automated_powervm_vlan_cleanup', False, 'AGENT')

        # Invoke
        self.agent.heal_and_optimize(False, [], [], [])

        # Verify.  One ensure call per net bridge.  Zero for the remove as that
        # has been flagged to not clean up.
        self.assertEqual(0, mock_nbr_remove.call_count)
        self.assertEqual(1, mock_prov_devs.call_count)

    @mock.patch('oslo_service.loopingcall.FixedIntervalLoopingCall')
    @mock.patch.object(ctx, 'get_admin_context_without_session',
                       return_value=mock.Mock())
    def test_setup_rpc(self, admin_ctxi, mock_loopingcall):
        """Validates that the setup_rpc method is properly invoked."""
        cfg.CONF.AGENT = mock.Mock()
        cfg.CONF.AGENT.report_interval = 5

        # Derives the instance that will be returned when a new loopingcall
        # is made.  Used for verification
        instance = mock_loopingcall.return_value

        # Run the method to completion
        self.agent.setup_rpc()

        # Make sure that the loopingcall had an interval of 5.
        instance.start.assert_called_with(interval=5)

    def test_get_nb_and_vlan(self):
        """Be sure nb uuid and vlan parsed from dev properly."""
        dev = FakeNPort('a', 100, 'physnet1')
        self.agent.br_map = {'physnet1': 'uuid1'}
        uuid, vlan = self.agent._get_nb_and_vlan(dev.rpc_device)
        self.assertEqual('uuid1', uuid)
        self.assertEqual(100, vlan)


class PVIDLooperTest(base.BasePVMTestCase):

    def setUp(self):
        super(PVIDLooperTest, self).setUp()

        self.mock_agent = mock.MagicMock()
        self.looper = sea_agent.PVIDLooper(self.mock_agent)

    def build_update_req(self, mac, lpar, vlan):
        dev = {'mac_address': mac, 'physical_network': 'default',
               'segmentation_id': vlan, 'device_owner': 'nova:compute'}
        preq = agent_base.ProvisionRequest(dev, lpar)
        return sea_agent.UpdateVLANRequest(preq)

    def test_add(self):
        req = sea_agent.UpdateVLANRequest(mock.MagicMock())
        self.assertEqual(0, len(self.looper.requests))
        self.looper.add(req)
        self.assertEqual(1, len(self.looper.requests))

    def test_add_with_duplicate(self):
        # Add a baseline request
        self.looper.add(self.build_update_req('aa', '1', 1))
        self.assertEqual(1, len(self.looper.requests))

        # Add a second request.  Same LPAR, different mac and vlan
        self.looper.add(self.build_update_req('bb', '1', 2))
        self.assertEqual(2, len(self.looper.requests))

        # Add a third request.  Different LPAR and VLAN, same mac. (VERY RARE,
        # maybe not possible in Neutron).
        self.looper.add(self.build_update_req('aa', '2', 1))
        self.assertEqual(3, len(self.looper.requests))

        # Add some duplicates.  Same mac and lpar.
        self.looper.add(self.build_update_req('aa', '1', 1))
        self.looper.add(self.build_update_req('bb', '1', 2))
        self.assertEqual(3, len(self.looper.requests))

    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.'
                'list_lpar_uuids')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.'
                'list_cnas')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.'
                'update_cna_pvid')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.'
                'find_cna_for_mac')
    def test_update(self, mock_find_cna_for_mac, mock_update_cna_pvid,
                    mock_list_cnas, mock_uuids):
        req = sea_agent.UpdateVLANRequest(FakeNPort('a', 27, 'phys_net'))
        self.looper.add(req)

        # Mock the element returned
        mock_cna = mock.MagicMock()
        mock_find_cna_for_mac.return_value = mock_cna
        mock_list_cnas.return_value = [mock_cna]
        mock_uuids.return_value = ['lpar_uuid']

        # Call the update
        self.looper.update()

        # Check to make sure the request was pulled off
        self.assertEqual(0, len(self.looper.requests))

        # Make sure the mock CNA had update called, and the vid set correctly
        mock_update_cna_pvid.assert_called_with(mock_cna, 27)

        # Make sure the port was updated
        self.assertFalse(self.mock_agent.update_device_down.called)
        self.assertTrue(self.mock_agent.update_device_up.called)

    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.'
                'list_lpar_uuids')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.'
                'list_cnas')
    def test_update_no_lpar(self, mock_list_cnas, mock_uuids):
        req = sea_agent.UpdateVLANRequest(FakeNPort('a', 27, 'phys_net'))
        self.looper.add(req)

        # Mock the element returned
        mock_uuids.return_value = []

        # Call the update
        self.looper.update()

        # Check to make sure the request was pulled off
        self.assertEqual(1, len(self.looper.requests))
        self.assertFalse(mock_list_cnas.called)

    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.'
                'list_lpar_uuids')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.'
                'list_cnas')
    @mock.patch('networking_powervm.plugins.ibm.agent.powervm.utils.'
                'find_cna_for_mac')
    def test_update_err(self, mock_find_cna_for_mac, mock_list_cnas,
                        mock_list_lpar_uuids):
        """Tests that the loop will error out after multiple loops."""
        mock_net_dev = FakeNPort('a', 1000, 'phys_net')

        req = sea_agent.UpdateVLANRequest(mock_net_dev)
        self.looper.add(req)

        # Mock the element returned
        mock_list_lpar_uuids.return_value = 'lpar_uuid'
        mock_list_cnas.return_value = [mock.Mock(mac='AABBCCDDEEFF', pvid=5)]
        mock_find_cna_for_mac.return_value = None

        loop_count = cfg.CONF.AGENT.pvid_update_loops

        for i in range(1, loop_count + 1):
            # Call the update
            self.looper.update()

            # Check to make sure the request was pulled off
            required_count = 1 if i < loop_count else 0
            self.assertEqual(required_count, len(self.looper.requests))

        self.assertTrue(self.mock_agent.update_device_down.called)
        self.assertFalse(self.mock_agent.update_device_up.called)
