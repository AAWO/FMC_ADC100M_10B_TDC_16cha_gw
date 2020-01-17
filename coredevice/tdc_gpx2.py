from artiq.coredevice import spi2 as spi
from artiq.coredevice.rtio import rtio_output, rtio_input_timestamped_data
from artiq.language.core import kernel, rpc
from artiq.language.types import TInt32
from artiq.language.units import ns
from coredevice.rtlink_csr import RtlinkCsr
from artiq.coredevice.ttl import TTLOut


SPI_CONFIG = (0*spi.SPI_OFFLINE | 0*spi.SPI_END |
              0*spi.SPI_INPUT | 0*spi.SPI_CS_POLARITY |
              1*spi.SPI_CLK_POLARITY | 1*spi.SPI_CLK_PHASE |
              0*spi.SPI_LSB_FIRST | 0*spi.SPI_HALF_DUPLEX)


class TDCGPX2ChannelDAQ:

    def __init__(self, dmgr, channel, data_width=44, core_device="core"):
        self.channel = channel
        self.core = dmgr.get(core_device)
        self.ref_period_mu = self.core.seconds_to_mu(
            self.core.coarse_ref_period)
        self.data_width = data_width
        self.samples_msb = []
        self.samples_lsb = []
        self.samples = []

    @kernel
    def open_gate(self):
        rtio_output((self.channel << 8), 1)
        # delay_mu(self.ref_period_mu)  # FIXME: Do we need that?

    @kernel
    def close_gate(self):
        rtio_output((self.channel << 8), 0)
        # delay_mu(self.ref_period_mu)  # FIXME: Do we need that?

    @rpc(flags={"async"})
    def _store_sample(self, sample, msb):
        if msb:
            self.samples_msb.append(sample)
        else:
            self.samples_lsb.append(sample)

    @kernel
    def _transfer_from_rtio(self, msb) -> TInt32:
        i = 0
        ch = self.channel if msb else self.channel+1
        while True:
            ts, data = rtio_input_timestamped_data(10*ns, ch)
            if ts < 0:
                break
            else:
                self._store_sample([ts, data], msb)
                i += 1
        return i

    def get_samples(self):
        self._transfer_from_rtio(msb=True)
        if self.data_width > 32:
            self._transfer_from_rtio(msb=False)
            for lsb, msb in zip(self.samples_lsb, self.samples_msb):
                self.samples.append([msb[0], (msb[1] << 32) | (lsb[1])])
        else:
            self.samples = self.samples_msb


class TDCGPX2:

    def __init__(self, dmgr, channel, spi_device, chip_select, spi_freq=25_000_000, data_width=44, core_device="core"):
        self.channel = channel
        self.core = dmgr.get(core_device)
        self.ref_period_mu = self.core.seconds_to_mu(
            self.core.coarse_ref_period)

        if isinstance(spi_device, str):
            self.spi = dmgr.get(spi_device)
        else:
            self.spi = spi_device  # type: spi.SPIMaster

        if isinstance(chip_select, TTLOut):
            self.chip_select = 0
            self.csn_device = chip_select
        else:
            self.chip_select = chip_select
            self.csn_device = None

        self.div = self.spi.frequency_to_div(spi_freq)

        phy_config = [
            [0, "frame_length", 6],
            [1, "frame_delay_value", 5],
            [2, "data_delay_value", 5]
        ]

        self.phy = [RtlinkCsr(dmgr, channel+i, config=phy_config, core_device=core_device) for i in range(4)]
        self.daq = [TDCGPX2ChannelDAQ(dmgr, channel+4+2*i, data_width, core_device) for i in range(4)]

    @kernel
    def _write_op(self, op, end=False):
        cs = self.chip_select
        if self.chip_select == 0:
            self.csn_device.off()
            cs = 1

        if end:
            flags = SPI_CONFIG | spi.SPI_END
        else:
            flags = SPI_CONFIG
        self.spi.set_config_mu(flags, 8, self.div, cs)
        self.spi.write((op & 0xFF) << 24)

        if end and self.chip_select == 0:
            self.csn_device.on()

    @kernel
    def _write_data(self, data):
        self.spi.set_config_mu(SPI_CONFIG | spi.SPI_END, 8, self.div, self.chip_select)
        self.spi.write((data & 0xFF) << 24)

    @kernel
    def read(self, addr) -> TInt32:
        cs = self.chip_select
        if self.chip_select == 0:
            self.csn_device.off()
            cs = 1

        self.spi.set_config_mu(SPI_CONFIG, 8, self.div, cs)
        self.spi.write(((0x60 | (addr & 0x1F)) & 0xFF) << 24)
        self.spi.set_config_mu(SPI_CONFIG | spi.SPI_INPUT | spi.SPI_END, 8, self.div, cs)
        self.spi.write(0)

        if self.chip_select == 0:
            self.csn_device.on()

        return self.spi.read()

    @kernel
    def write(self, addr, data):
        self._write_op(0x80 | (addr & 0x1F), end=False)
        self._write_data(data & 0xFF)

        if self.chip_select == 0:
            self.csn_device.on()

    @kernel
    def power_on_reset(self):
        self._write_op(0x30, end=True)

    @kernel
    def initialization_reset(self):
        self._write_op(0x18, end=True)






