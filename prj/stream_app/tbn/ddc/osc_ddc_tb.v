`timescale 1ns / 1ps

// Drives osc_ddc with ddc_in.hex (one 16-bit sample per 8 ns clock) and
// writes every output word to ddc_rtl_out.txt for comparison against
// ddc_model.py. Run with sim_ddc.sh.

module osc_ddc_tb;

localparam N_IN = 40000;

reg                clk = 1'b0;
reg                rst_n = 1'b0;
reg  [16-1:0]      in_mem [0:N_IN-1];
reg  [32-1:0]      pinc_mem [0:0];
reg  [16-1:0]      s_tdata = 'h0;
reg                s_tvalid = 1'b0;
wire [16-1:0]      m_tdata;
wire               m_tvalid;
integer            n, fd, n_out;

always #4 clk = ~clk;   // 125 MHz

osc_ddc U_dut (
  .clk           (clk),
  .rst_n         (rst_n),
  .s_axis_tdata  (s_tdata),
  .s_axis_tvalid (s_tvalid),
  .s_axis_tready (),
  .m_axis_tdata  (m_tdata),
  .m_axis_tvalid (m_tvalid),
  .m_axis_tready (1'b1),
  .cfg_pinc      (pinc_mem[0]));

always @(posedge clk) begin
  if (m_tvalid) begin
    $fdisplay(fd, "%0d", $signed(m_tdata));
    n_out = n_out + 1;
  end
end

initial begin
  $readmemh("ddc_in.hex",   in_mem);
  $readmemh("ddc_pinc.hex", pinc_mem);
  fd    = $fopen("ddc_rtl_out.txt", "w");
  n_out = 0;
  repeat (10) @(posedge clk);
  rst_n <= 1'b1;
  repeat (5) @(posedge clk);
  for (n = 0; n < N_IN; n = n + 1) begin
    s_tdata  <= in_mem[n];
    s_tvalid <= 1'b1;
    @(posedge clk);
  end
  s_tvalid <= 1'b0;
  repeat (200) @(posedge clk);
  $fclose(fd);
  $display("osc_ddc_tb: %0d input samples, %0d output words", N_IN, n_out);
  $finish;
end

endmodule
