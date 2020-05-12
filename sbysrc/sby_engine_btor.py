#
# SymbiYosys (sby) -- Front-end for Yosys-based formal verification flows
#
# Copyright (C) 2016  Clifford Wolf <clifford@clifford.at>
#
# Permission to use, copy, modify, and/or distribute this software for any
# purpose with or without fee is hereby granted, provided that the above
# copyright notice and this permission notice appear in all copies.
#
# THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
# WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
# MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
# ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
# WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
# ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
# OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
#

import re, os, getopt
from types import SimpleNamespace
from sby_core import SbyTask

def run(mode, job, engine_idx, engine):
    opts, solver_args = getopt.getopt(engine[1:], "", [])

    if len(solver_args) == 0:
        job.error("Missing solver command.")

    for o, a in opts:
        job.error("Unexpected BTOR engine options.")

    if solver_args[0] == "btormc":
        solver_cmd = job.exe_paths["btormc"] + " --stop-first {} -v 1 -kmax {}".format(0 if mode == "cover" else 1, job.opt_depth - 1)
        if job.opt_skip is not None:
            solver_cmd += " -kmin {}".format(job.opt_skip)
        solver_cmd += " ".join([""] + solver_args[1:])

    else:
        job.error("Invalid solver command {}.".format(solver_args[0]))

    common_state = SimpleNamespace()
    common_state.solver_status = None
    common_state.produced_cex = 0
    common_state.expected_cex = 1
    common_state.wit_file = None
    common_state.assert_fail = False
    common_state.produced_traces = []
    common_state.print_traces_max = 5
    common_state.running_tasks = 0

    def print_traces_and_terminate():
        if mode == "cover":
            if common_state.assert_fail:
                task_status = "FAIL"
            elif common_state.solver_status == "sat":
                task_status = "PASS"
            elif common_state.solver_status == "unsat":
                task_status = "FAIL"
            else:
                job.error("engine_{}: Engine terminated without status.".format(engine_idx))
        else:
            if common_state.solver_status == "sat":
                task_status = "FAIL"
            elif common_state.solver_status == "unsat":
                task_status = "PASS"
            else:
                job.error("engine_{}: Engine terminated without status.".format(engine_idx))

        job.update_status(task_status)
        job.log("engine_{}: Status returned by engine: {}".format(engine_idx, task_status))
        job.summary.append("engine_{} ({}) returned {}".format(engine_idx, " ".join(engine), task_status))

        common_state.produced_traces.sort()
        if len(common_state.produced_traces) == 0:
            job.log("engine_{}: Engine did not produce a counter example.".format(engine_idx))
        elif len(common_state.produced_traces) < common_state.print_traces_max:
            job.summary.extend(common_state.produced_traces)
        else:
            job.summary.extend(common_state.produced_traces[:common_state.print_traces_max])
            excess_traces = len(common_state.produced_traces) - common_state.print_traces_max
            job.summary.append("and {} further trace{}".format(excess_traces, "s" if excess_traces > 1 else {}))

    if mode == "cover":
        def output_callback2(line):
            match = re.search(r"Assert failed in test", line)
            if match:
                common_state.assert_fail = True
            return line
    else:
        def output_callback2(line):
            return line

    def make_exit_callback(suffix):
        def exit_callback2(retcode):
            assert retcode == 0

            vcdpath = "{}/engine_{}/trace{}.vcd".format(job.workdir, engine_idx, suffix)
            if os.path.exists(vcdpath):
                common_state.produced_traces.append("{}trace: {}".format("" if mode == "cover" else "counterexample ", vcdpath))

            common_state.running_tasks -= 1
            if (common_state.running_tasks == 0):
                print_traces_and_terminate()

        return exit_callback2

    def output_callback(line):
        if mode == "cover":
            match = re.search(r"calling BMC on ([0-9]+) properties", line)
            if match:
                common_state.expected_cex = int(match[1])
                assert common_state.expected_cex > 0
                assert common_state.produced_cex == 0

        if (common_state.produced_cex < common_state.expected_cex) and line == "sat":
            assert common_state.wit_file == None
            if common_state.expected_cex == 1:
                common_state.wit_file = open("{}/engine_{}/trace.wit".format(job.workdir, engine_idx), "w")
            else:
                common_state.wit_file = open("{}/engine_{}/trace{}.wit".format(job.workdir, engine_idx, common_state.produced_cex), "w")

        if common_state.wit_file:
            print(line, file=common_state.wit_file)
            if line == ".":
                if common_state.expected_cex == 1:
                    suffix = ""
                else:
                    suffix = common_state.produced_cex
                task2 = SbyTask(job, "engine_{}".format(engine_idx), job.model("btor"),
                        "cd {dir} ; btorsim -c --vcd engine_{idx}/trace{i}.vcd --hierarchical-symbols --info model/design_btor.info model/design_btor.btor engine_{idx}/trace{i}.wit".format(dir=job.workdir, idx=engine_idx, i=suffix),
                        logfile=open("{dir}/engine_{idx}/logfile2.txt".format(dir=job.workdir, idx=engine_idx), "w"))
                task2.output_callback = output_callback2
                task2.exit_callback = make_exit_callback(suffix)
                task2.checkretcode = True
                common_state.running_tasks += 1

                common_state.produced_cex += 1
                common_state.wit_file.close()
                common_state.wit_file = None
                if common_state.produced_cex == common_state.expected_cex:
                    common_state.solver_status = "sat"

        if line.startswith("u"):
            return "No CEX up to depth {}.".format(int(line[1:])-1)

        if solver_args[0] == "btormc":
            if "calling BMC on" in line:
                return line
            if "SATISFIABLE" in line:
                return line
            if "bad state properties at bound" in line:
                return line
            if "deleting model checker:" in line:
                if common_state.solver_status is None:
                    common_state.solver_status = "unsat"
                return line

        if not common_state.wit_file:
            print(line, file=task.logfile)

        return None

    def exit_callback(retcode):
        assert retcode == 0
        assert common_state.solver_status is not None

        if common_state.solver_status == "unsat":
            if common_state.expected_cex == 1:
                with open("{}/engine_{}/trace.wit".format(job.workdir, engine_idx), "w") as wit_file:
                    print("unsat", file=wit_file)
            else:
                for i in range(common_state.produced_cex, common_state.expected_cex):
                    with open("{}/engine_{}/trace{}.wit".format(job.workdir, engine_idx, i), "w") as wit_file:
                        print("unsat", file=wit_file)

        common_state.running_tasks -= 1
        if (common_state.running_tasks == 0):
            print_traces_and_terminate()

    task = SbyTask(job, "engine_{}".format(engine_idx), job.model("btor"),
            "cd {}; {} model/design_btor.btor".format(job.workdir, solver_cmd),
            logfile=open("{}/engine_{}/logfile.txt".format(job.workdir, engine_idx), "w"))

    task.output_callback = output_callback
    task.exit_callback = exit_callback
    common_state.running_tasks += 1
