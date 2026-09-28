`timescale 1ns / 1ps

// Symmetric decimate-by-2 FIR for I and Q, time-multiplexed.
//
// Input arrives at most once every 10 clocks, output is produced after every
// second input, so there are >= 20 clocks per output. Because the taps are
// symmetric (h[k] = h[TAPS-1-k]) the two samples sharing a coefficient are
// added first, so a TAPS=40 filter needs only 20 multiply-accumulates. One
// multiplier per I/Q path does all of them, one tap per clock.
//
// Samples are kept in a 64-entry circular buffer; the newest sample of the
// pair that started a computation is "run_base", and tap pair k reads
// run_base-k and run_base-(TAPS-1)+k. Computations can run back to back, so
// run_base only changes when the next one starts.

module osc_ddc_fir
  #(parameter IN_BITS   = 18,
    parameter COEF_BITS = 18,
    parameter TAPS      = 40,
    parameter SHIFT     = 20,
    parameter OUT_BITS  = 15)(
  input  wire                        clk,
  input  wire                        rst_n,
  input  wire signed [IN_BITS-1:0]   in_i,
  input  wire signed [IN_BITS-1:0]   in_q,
  input  wire                        in_vld,
  output reg  signed [OUT_BITS-1:0]  out_i,
  output reg  signed [OUT_BITS-1:0]  out_q,
  output reg                         out_vld
);

localparam HALF     = TAPS/2;
localparam ACC_BITS = 48;

////////////////////////////////////////////////////////////
// Sample buffer
////////////////////////////////////////////////////////////

reg signed [IN_BITS-1:0] buf_i [0:63];
reg signed [IN_BITS-1:0] buf_q [0:63];
reg        [      6-1:0] wp;
reg                      phase;       // toggles on every input, output on the second of a pair
reg                      start;
reg        [      6-1:0] base;

integer m;
initial begin
  for (m = 0; m < 64; m = m + 1) begin
    buf_i[m] = 'h0;
    buf_q[m] = 'h0;
  end
end

always @(posedge clk) begin
  if (in_vld) begin
    buf_i[wp] <= in_i;
    buf_q[wp] <= in_q;
  end
end

always @(posedge clk) begin
  if (rst_n == 1'b0) begin
    wp    <= 'h0;
    phase <= 1'b0;
    start <= 1'b0;
    base  <= 'h0;
  end else begin
    start <= in_vld & phase;
    if (in_vld) begin
      wp    <= wp + 1'b1;
      phase <= ~phase;
    end
    if (in_vld & phase)
      base  <= wp;
  end
end

////////////////////////////////////////////////////////////
// Tap sequencer
////////////////////////////////////////////////////////////

reg          run;
reg  [5-1:0] k;
reg  [6-1:0] run_base;   // held for the whole computation
wire [6-1:0] addr_a = run_base - k;
wire [6-1:0] addr_b = run_base - (TAPS-1) + k;

always @(posedge clk) begin
  if (rst_n == 1'b0) begin
    run      <= 1'b0;
    k        <= 'h0;
    run_base <= 'h0;
  end else begin
    if (start) begin
      run      <= 1'b1;
      k        <= 'h0;
      run_base <= base;
    end else if (run) begin
      if (k == HALF-1)
        run <= 1'b0;
      k <= k + 1'b1;
    end
  end
end

////////////////////////////////////////////////////////////
// MAC pipeline: read -> pre-add -> multiply -> accumulate
////////////////////////////////////////////////////////////

wire signed [COEF_BITS-1:0] coef;

osc_ddc_fir_coef U_coef (
  .clk  (clk),
  .addr (k),
  .dat  (coef));

reg signed [IN_BITS-1:0]            ia, ib, qa, qb;
reg signed [IN_BITS:0]              i_sum, q_sum;
reg signed [COEF_BITS-1:0]          coef_r;
reg signed [IN_BITS+COEF_BITS:0]    i_prod, q_prod;
reg signed [ACC_BITS-1:0]           i_acc, q_acc;
reg [4-1:0]                         first_sr, last_sr, run_sr;

always @(posedge clk) begin
  // stage 1: buffer read (coefficient ROM read in parallel)
  ia <= buf_i[addr_a];
  ib <= buf_i[addr_b];
  qa <= buf_q[addr_a];
  qb <= buf_q[addr_b];
  // stage 2: symmetric pre-add
  i_sum  <= ia + ib;
  q_sum  <= qa + qb;
  coef_r <= coef;
  // stage 3: multiply
  i_prod <= i_sum * coef_r;
  q_prod <= q_sum * coef_r;
end

always @(posedge clk) begin
  if (rst_n == 1'b0) begin
    run_sr   <= 'h0;
    first_sr <= 'h0;
    last_sr  <= 'h0;
    i_acc    <= 'h0;
    q_acc    <= 'h0;
  end else begin
    run_sr   <= {run_sr[2:0],   run};
    first_sr <= {first_sr[2:0], run & (k == 0)};
    last_sr  <= {last_sr[2:0],  run & (k == HALF-1)};
    // stage 4: accumulate, restarting on the first tap
    if (run_sr[2] & first_sr[2]) begin
      i_acc <= i_prod;
      q_acc <= q_prod;
    end else if (run_sr[2]) begin
      i_acc <= i_acc + i_prod;
      q_acc <= q_acc + q_prod;
    end
  end
end

////////////////////////////////////////////////////////////
// Round, saturate, output
////////////////////////////////////////////////////////////

function signed [OUT_BITS-1:0] round_sat;
  input signed [ACC_BITS-1:0] acc;
  reg   signed [ACC_BITS-1:0] r;
  begin
    r = (acc + (48'sd1 <<< (SHIFT-1))) >>> SHIFT;
    if (r > $signed({1'b0, {(OUT_BITS-1){1'b1}}}))
      round_sat = {1'b0, {(OUT_BITS-1){1'b1}}};
    else if (r < $signed({{(ACC_BITS-OUT_BITS+1){1'b1}}, {(OUT_BITS-1){1'b0}}}))
      round_sat = {1'b1, {(OUT_BITS-1){1'b0}}};
    else
      round_sat = r[OUT_BITS-1:0];
  end
endfunction

always @(posedge clk) begin
  if (rst_n == 1'b0) begin
    out_vld <= 1'b0;
    out_i   <= 'h0;
    out_q   <= 'h0;
  end else begin
    out_vld <= last_sr[3];
    if (last_sr[3]) begin
      out_i <= round_sat(i_acc);
      out_q <= round_sat(q_acc);
    end
  end
end

endmodule
