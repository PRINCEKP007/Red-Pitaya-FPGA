`timescale 1ns / 1ps

// Compressive measurement: for every block of cfg_blk_len (N) input samples,
// output one weighted sum
//
//     y[m] = sum_{i=0}^{N-1} w[pos] * x[m*N + i],   pos = (m*N + i) mod cfg_tbl_len
//
// The weights come from a 1024-entry table (osc_cs_tbl) that software can
// rewrite. cfg_tbl_len (L) must be a multiple of N, giving a repeating
// block-diagonal measurement matrix Phi with L/N rows.
//
//   x ─► [× w] ─► [accumulate N] ─► [round, >> shift] ─► {y[14:0], frame}
//          ▲
//     weight table[pos]
//
// Output word: bits [15:1] = y (saturated), bit 0 = 1 when the block used
// row 0 of Phi (pos = 0 at its first sample), so the host can align.
// Counters restart from 0 while cfg_en is low.
// Bit-exact model: prj/stream_app/cs/cs_model.py

module osc_cs
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
  input  wire                       cfg_en,
  input  wire [11-1:0]              cfg_blk_len,   // N, 1..1024
  input  wire [11-1:0]              cfg_tbl_len,   // L, 1..1024, multiple of N
  input  wire [ 5-1:0]              cfg_shift,     // output = acc >> shift (acc in Q2.30)
  // Weight table write port
  input  wire                       tbl_we,
  input  wire [10-1:0]              tbl_waddr,
  input  wire [16-1:0]              tbl_wdata
);

localparam ACC_BITS = 48;
localparam OUT_BITS = 15;

assign s_axis_tready = 1'b1;

////////////////////////////////////////////////////////////
// Position counters: pos walks through the weight table,
// blk counts samples inside the current block
////////////////////////////////////////////////////////////

reg  [10-1:0] pos;
reg  [10-1:0] blk;
wire          blk_first = (blk == 0);
wire          blk_last  = (blk >= cfg_blk_len - 1);

always @(posedge clk) begin
  if (rst_n == 1'b0 || cfg_en == 1'b0) begin
    pos <= 'h0;
    blk <= 'h0;
  end else if (s_axis_tvalid) begin
    blk <= blk_last ? 'h0 : blk + 1'b1;
    pos <= (pos >= cfg_tbl_len - 1) ? 'h0 : pos + 1'b1;
  end
end

////////////////////////////////////////////////////////////
// Stage 1: weight table read (x delayed to line up)
////////////////////////////////////////////////////////////

wire signed [16-1:0] w;
reg  signed [16-1:0] x_d;
reg                  vld_d, first_d, last_d, frame_d;

osc_cs_tbl U_tbl (
  .clk   (clk),
  .we    (tbl_we),
  .waddr (tbl_waddr),
  .wdata (tbl_wdata),
  .raddr (pos),
  .rdata (w));

always @(posedge clk) begin
  x_d     <= s_axis_tdata;
  vld_d   <= s_axis_tvalid & cfg_en;
  first_d <= blk_first;
  last_d  <= blk_last;
  frame_d <= blk_first & (pos == 0);
end

////////////////////////////////////////////////////////////
// Stage 2: multiply, stage 3: accumulate over the block
////////////////////////////////////////////////////////////

reg  signed [32-1:0]       prod;
reg                        vld_p, first_p, last_p, frame_p;
reg  signed [ACC_BITS-1:0] acc;
reg                        frame_blk;
reg                        done;

always @(posedge clk) begin
  prod    <= x_d * w;
  first_p <= first_d;
  last_p  <= last_d;
  frame_p <= frame_d;
end

always @(posedge clk) begin
  if (rst_n == 1'b0) begin
    vld_p     <= 1'b0;
    acc       <= 'h0;
    frame_blk <= 1'b0;
    done      <= 1'b0;
  end else begin
    vld_p <= vld_d;
    done  <= vld_p & last_p;
    if (vld_p) begin
      if (first_p) begin
        acc       <= prod;
        frame_blk <= frame_p;
      end else begin
        acc       <= acc + prod;
      end
    end
  end
end

////////////////////////////////////////////////////////////
// Stage 4: round, saturate to 15 bits, add frame flag
////////////////////////////////////////////////////////////

wire signed [ACC_BITS-1:0] rnd    = (48'sd1 <<< cfg_shift) >>> 1;
wire signed [ACC_BITS-1:0] scaled = (acc + rnd) >>> cfg_shift;
wire signed [ACC_BITS-1:0] max_v  = (48'sd1 <<< (OUT_BITS-1)) - 1;
wire signed [ACC_BITS-1:0] min_v  = -(48'sd1 <<< (OUT_BITS-1));
wire        [OUT_BITS-1:0] y      = (scaled > max_v) ? max_v[OUT_BITS-1:0] :
                                    (scaled < min_v) ? min_v[OUT_BITS-1:0] :
                                                       scaled[OUT_BITS-1:0];

always @(posedge clk) begin
  if (rst_n == 1'b0) begin
    m_axis_tvalid <= 1'b0;
    m_axis_tdata  <= 'h0;
  end else begin
    m_axis_tvalid <= done;
    if (done)
      m_axis_tdata <= {y, frame_blk};
  end
end

endmodule
