`timescale 1ns / 1ps

// Drives osc_cs with cs_in.hex (one 16-bit sample per 8 ns clock) using the
// configuration and weight-table writes in cs_cfg.hex, and writes every
// output word to cs_rtl_out.txt for comparison against cs_model.py.
// Run with sim_cs.sh.

module osc_cs_tb;

localparam N_IN = 12000;

reg                clk = 1'b0;
reg                rst_n = 1'b0;
reg  [16-1:0]      in_mem  [0:N_IN-1];
reg  [32-1:0]      cfg_mem [0:63];
reg  [16-1:0]      s_tdata = 'h0;
reg                s_tvalid = 1'b0;
reg                cfg_en = 1'b0;
reg                tbl_we = 1'b0;
reg  [10-1:0]      tbl_waddr = 'h0;
reg  [16-1:0]      tbl_wdata = 'h0;
wire [16-1:0]      m_tdata;
wire               m_tvalid;
integer            n, fd, n_out;

always #4 clk = ~clk;   // 125 MHz

osc_cs U_dut (
  .clk           (clk),
  .rst_n         (rst_n),
  .s_axis_tdata  (s_tdata),
  .s_axis_tvalid (s_tvalid),
  .s_axis_tready (),
  .m_axis_tdata  (m_tdata),
  .m_axis_tvalid (m_tvalid),
  .m_axis_tready (1'b1),
  .cfg_en        (cfg_en),
  .cfg_blk_len   (cfg_mem[0][10:0]),
  .cfg_tbl_len   (cfg_mem[1][10:0]),
  .cfg_shift     (cfg_mem[2][4:0]),
  .tbl_we        (tbl_we),
  .tbl_waddr     (tbl_waddr),
  .tbl_wdata     (tbl_wdata));

always @(posedge clk) begin
  if (m_tvalid) begin
    $fdisplay(fd, "%0d", $signed(m_tdata));
    n_out = n_out + 1;
  end
end

initial begin
  $readmemh("cs_in.hex",  in_mem);
  $readmemh("cs_cfg.hex", cfg_mem);
  fd    = $fopen("cs_rtl_out.txt", "w");
  n_out = 0;
  repeat (10) @(posedge clk);
  rst_n <= 1'b1;
  // weight table writes
  for (n = 0; n < cfg_mem[3]; n = n + 1) begin
    tbl_we    <= 1'b1;
    tbl_waddr <= cfg_mem[4+n][25:16];
    tbl_wdata <= cfg_mem[4+n][15:0];
    @(posedge clk);
  end
  tbl_we <= 1'b0;
  // idle samples before enabling must not produce output
  repeat (20) begin
    s_tdata  <= 16'h1234;
    s_tvalid <= 1'b1;
    @(posedge clk);
  end
  s_tvalid <= 1'b0;
  cfg_en   <= 1'b1;
  repeat (3) @(posedge clk);
  for (n = 0; n < N_IN; n = n + 1) begin
    s_tdata  <= in_mem[n];
    s_tvalid <= 1'b1;
    @(posedge clk);
  end
  s_tvalid <= 1'b0;
  repeat (50) @(posedge clk);
  $fclose(fd);
  $display("osc_cs_tb: %0d input samples, %0d output words", N_IN, n_out);
  $finish;
end

endmodule
