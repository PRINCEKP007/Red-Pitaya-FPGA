`timescale 1ns / 1ps

// Digital down-converter: real 125 MSPS ADC stream -> complex baseband.
//
//   x[n] --+--> x*cos --> CIC /10 --+--> FIR /2 --> I --+
//          |                        |                   +--> I,Q,I,Q,... 16-bit words
//          +--> x*-sin -> CIC /10 --+--> FIR /2 --> Q --+
//                ^
//               NCO: phase += cfg_pinc every sample, fc = cfg_pinc * 125 MHz / 2^32
//
// Output is fc +/- 2.5 MHz at 6.25 MSPS complex: 12.5 Mwords/s, 25 MB/s.
// Each output word is a 15-bit value in bits [15:1] with a tag in bit 0
// (0 = I, 1 = Q), so the host can find the I/Q pairing anywhere in the
// stream. A real tone of amplitude A comes out with |I + jQ| = A/2.
// Bit-exact model: prj/stream_app/ddc/ddc_model.py

module osc_ddc
  #(parameter AXIS_DATA_BITS = 16)(
  input  wire                       clk,
  input  wire                       rst_n,
  // Slave AXI-S
  input  wire [AXIS_DATA_BITS-1:0]  s_axis_tdata,
  input  wire                       s_axis_tvalid,
  output wire                       s_axis_tready,
  // Master AXI-S
  output reg  [AXIS_DATA_BITS-1:0]  m_axis_tdata,
  output reg                        m_axis_tvalid,
  input  wire                       m_axis_tready,
  // Config
  input  wire [32-1:0]              cfg_pinc
);

localparam LUT_BITS  = 10;
localparam MIX_BITS  = 18;
localparam MIX_SHIFT = 13;
localparam CIC_R     = 10;

assign s_axis_tready = 1'b1;

////////////////////////////////////////////////////////////
// NCO
////////////////////////////////////////////////////////////

reg  [32-1:0]             phase;
reg  signed [16-1:0]      x_d;
reg                       vld_d;
wire signed [16-1:0]      nco_sin;
wire signed [16-1:0]      nco_cos;
wire [LUT_BITS-1:0]       addr_sin = phase[32-1:32-LUT_BITS];
wire [LUT_BITS-1:0]       addr_cos = addr_sin + (1 << (LUT_BITS-2));   // +90 degrees

osc_ddc_sin_rom U_sin_rom (
  .clk    (clk),
  .addr_a (addr_sin),
  .addr_b (addr_cos),
  .dat_a  (nco_sin),
  .dat_b  (nco_cos));

always @(posedge clk) begin
  if (rst_n == 1'b0) begin
    phase <= 'h0;
    vld_d <= 1'b0;
  end else begin
    vld_d <= s_axis_tvalid;
    if (s_axis_tvalid)
      phase <= phase + cfg_pinc;
  end
  x_d <= s_axis_tdata;   // aligned with the ROM output
end

////////////////////////////////////////////////////////////
// Mixer: I = x*cos, Q = -x*sin, rounded to 18 bits
////////////////////////////////////////////////////////////

reg  signed [32-1:0]       prod_i, prod_q;
reg  signed [MIX_BITS-1:0] mix_i,  mix_q;
reg                        vld_p,  vld_m;

wire signed [32-1:0] rnd_i = (prod_i + (32'sd1 <<< (MIX_SHIFT-1))) >>> MIX_SHIFT;
wire signed [32-1:0] rnd_q = (prod_q + (32'sd1 <<< (MIX_SHIFT-1))) >>> MIX_SHIFT;

always @(posedge clk) begin
  prod_i <= x_d * nco_cos;
  prod_q <= x_d * nco_sin;
  mix_i  <=  rnd_i[MIX_BITS-1:0];
  mix_q  <= -rnd_q[MIX_BITS-1:0];
end

always @(posedge clk) begin
  if (rst_n == 1'b0) begin
    vld_p <= 1'b0;
    vld_m <= 1'b0;
  end else begin
    vld_p <= vld_d;
    vld_m <= vld_p;
  end
end

////////////////////////////////////////////////////////////
// CIC decimate by 10
////////////////////////////////////////////////////////////

reg  [4-1:0]          dec_cnt;
wire                  dec_stb = (dec_cnt == CIC_R-1);
wire signed [18-1:0]  cic_i, cic_q;
wire                  cic_vld;

always @(posedge clk) begin
  if (rst_n == 1'b0)
    dec_cnt <= 'h0;
  else if (vld_m)
    dec_cnt <= dec_stb ? 'h0 : dec_cnt + 1'b1;
end

osc_ddc_cic #(
  .IN_BITS (MIX_BITS), .OUT_BITS (18), .N (5), .R (CIC_R), .W (35), .OUT_SHIFT (17))
  U_cic_i (
  .clk     (clk),
  .rst_n   (rst_n),
  .in_dat  (mix_i),
  .in_vld  (vld_m),
  .dec_stb (dec_stb),
  .out_dat (cic_i),
  .out_vld (cic_vld));

osc_ddc_cic #(
  .IN_BITS (MIX_BITS), .OUT_BITS (18), .N (5), .R (CIC_R), .W (35), .OUT_SHIFT (17))
  U_cic_q (
  .clk     (clk),
  .rst_n   (rst_n),
  .in_dat  (mix_q),
  .in_vld  (vld_m),
  .dec_stb (dec_stb),
  .out_dat (cic_q),
  .out_vld ());

////////////////////////////////////////////////////////////
// FIR decimate by 2 (CIC droop compensation + final band limit)
////////////////////////////////////////////////////////////

wire signed [15-1:0] fir_i, fir_q;
wire                 fir_vld;

osc_ddc_fir #(
  .IN_BITS (18), .COEF_BITS (18), .TAPS (40), .SHIFT (20), .OUT_BITS (15))
  U_fir (
  .clk     (clk),
  .rst_n   (rst_n),
  .in_i    (cic_i),
  .in_q    (cic_q),
  .in_vld  (cic_vld),
  .out_i   (fir_i),
  .out_q   (fir_q),
  .out_vld (fir_vld));

////////////////////////////////////////////////////////////
// Output: I word (tag 0) then Q word (tag 1) on consecutive clocks
////////////////////////////////////////////////////////////

reg                  q_pend;
reg signed [15-1:0]  q_hold;

always @(posedge clk) begin
  if (rst_n == 1'b0) begin
    m_axis_tvalid <= 1'b0;
    m_axis_tdata  <= 'h0;
    q_pend        <= 1'b0;
    q_hold        <= 'h0;
  end else begin
    if (fir_vld) begin
      m_axis_tdata  <= {fir_i, 1'b0};
      m_axis_tvalid <= 1'b1;
      q_hold        <= fir_q;
      q_pend        <= 1'b1;
    end else if (q_pend) begin
      m_axis_tdata  <= {q_hold, 1'b1};
      m_axis_tvalid <= 1'b1;
      q_pend        <= 1'b0;
    end else begin
      m_axis_tvalid <= 1'b0;
    end
  end
end

endmodule
