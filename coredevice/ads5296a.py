from artiq.coredevice import spi2 as spi
from artiq.coredevice.rtio import rtio_output, rtio_input_timestamped_data
from artiq.language import TInt32
from artiq.language.core import kernel, delay_mu
from artiq.language.core import rpc
from artiq.language.units import us
from coredevice.rtlink_csr import RtlinkCsr


SPI_CONFIG = (0*spi.SPI_OFFLINE | 0*spi.SPI_END |
              0*spi.SPI_INPUT | 0*spi.SPI_CS_POLARITY |
              0*spi.SPI_CLK_POLARITY | 0*spi.SPI_CLK_PHASE |
              0*spi.SPI_LSB_FIRST | 0*spi.SPI_HALF_DUPLEX)


class AdcDaq:

    def __init__(self, dmgr, channel, core_device="core"):
        self.channel = channel
        self.core = dmgr.get(core_device)
        self.ref_period_mu = self.core.seconds_to_mu(
            self.core.coarse_ref_period)
        self.pretrigger = 0
        self.posttrigger = 0
        self.incomplete = False
        self.samples = []

    @kernel
    def configure(self, pretrigger, posttrigger):
        rtio_output((self.channel << 8) | 1,
                    (pretrigger << 12) | posttrigger)
        delay_mu(self.ref_period_mu)
        self.pretrigger = pretrigger
        self.posttrigger = posttrigger

    @kernel
    def trigger(self):
        rtio_output((self.channel << 8) | 0, 0)  # data is not important, stb is used as a trigger
        delay_mu(self.ref_period_mu)

    @rpc(flags={"async"})
    def store_sample(self, sample):
        self.samples.append(sample)

    @kernel
    def get_samples(self):
        self.incomplete = False
        for _ in range(self.pretrigger+self.posttrigger):
            ts, data = rtio_input_timestamped_data(1*us, self.channel)
            if ts < 0:
                self.incomplete = True
                break
            else:
                self.store_sample(data)


class ADS5296A:

    def __init__(self, dmgr, channel, spi_device, chip_select, spi_freq=10_000_000, core_device="core"):
        self.channel = channel
        self.core = dmgr.get(core_device)
        self.ref_period_mu = self.core.seconds_to_mu(
            self.core.coarse_ref_period)

        if isinstance(spi_device, str):
            self.spi = dmgr.get(spi_device)
        else:
            self.spi = spi_device  # type: spi.SPIMaster
        self.chip_select = chip_select

        self.div = self.spi.frequency_to_div(spi_freq)

        phy_config = [
            [0, "data0_delay_value", 5],
            [1, "data1_delay_value", 5],
            [2, "data2_delay_value", 5],
            [3, "data3_delay_value", 5],
            [4, "data4_delay_value", 5],
            [5, "data5_delay_value", 5],
            [6, "data6_delay_value", 5],
            [7, "data7_delay_value", 5],
            [8, "adclk_delay_value", 5]
        ]

        self.phy = RtlinkCsr(dmgr, channel, config=phy_config, core_device=core_device)
        self.daq = [AdcDaq(dmgr, channel+1+i, core_device) for i in range(8)]

    @kernel
    def write(self, addr, data):
        self.spi.set_config_mu(SPI_CONFIG | spi.SPI_END, 24, self.div, self.chip_select)
        self.spi.write(((addr & 0xFF) << 16) | (data & 0xFFFF))

    @kernel
    def read(self, addr) -> TInt32:
        self.write(0x1, 1)
        self.spi.set_config_mu(SPI_CONFIG, 8, self.div, self.chip_select)
        self.spi.write(addr & 0xFF)
        self.spi.set_config_mu(SPI_CONFIG | spi.SPI_INPUT | spi.SPI_END, 16, self.div, self.chip_select)
        return self.spi.read()
