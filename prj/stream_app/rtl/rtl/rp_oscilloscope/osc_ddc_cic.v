`timescale 1ns / 1ps

// CIC decimator: N integrators at the input rate, decimate by R, N combs at
// the output rate. No multipliers - only adders. Register width must be
// IN_BITS + ceil(N*log2(R)); intermediate overflow wraps around harmlessly.
// Gain is R^N, so the output is taken OUT_SHIFT bits down.

module osc_ddc_cic
  #(parameter IN_BITS   = 18,
    parameter OUT_BITS  = 18,
    parameter N         = 5,
    parameter R         = 10,
    parameter W         = 35,
    parameter OUT_SHIFT = 17)(
  input  wire                        clk,
  input  wire                        rst_n,
  input  wire signed [IN_BITS-1:0]   in_dat,
  input  wire                        in_vld,
  input  wire                        dec_stb,   // high on the input sample that ends a decimation period
  output wire signed [OUT_BITS-1:0]  out_dat,
  output reg                         out_vld
);

reg signed [W-1:0] integ [0:N-1];
reg signed [W-1:0] comb  [0:N-1];
reg signed [W-1:0] dly   [0:N-1];
reg signed [W-1:0] samp;
reg                samp_vld;

integer s;

always @(posedge clk) begin
  if (rst_n == 1'b0) begin
    for (s = 0; s < N; s = s + 1) begin
      integ[s] <= 'h0;
      comb[s]  <= 'h0;
      dly[s]   <= 'h0;
    end
    samp     <= 'h0;
    samp_vld <= 1'b0;
    out_vld  <= 1'b0;
  end else begin
    // integrators, one pipeline register per stage
    if (in_vld) begin
      integ[0] <= integ[0] + in_dat;
      for (s = 1; s < N; s = s + 1)
        integ[s] <= integ[s] + integ[s-1];
    end

    // decimate: keep one of every R integrator outputs
    samp_vld <= in_vld & dec_stb;
    if (in_vld & dec_stb)
      samp <= integ[N-1];

    // combs y = x[m] - x[m-1], one pipeline register per stage
    if (samp_vld) begin
      comb[0] <= samp - dly[0];
      dly[0]  <= samp;
      for (s = 1; s < N; s = s + 1) begin
        comb[s] <= comb[s-1] - dly[s];
        dly[s]  <= comb[s-1];
      end
    end
    out_vld <= samp_vld;
  end
end

assign out_dat = comb[N-1][OUT_SHIFT+OUT_BITS-1:OUT_SHIFT];

endmodule
