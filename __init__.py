from binaryninja.architecture import Architecture, ArchitectureHook, Endianness
from binaryninja.enums import Endianness
from binaryninja import (
    Architecture, RegisterInfo, InstructionInfo,
    InstructionTextToken, InstructionTextTokenType,
    BranchType,
    LowLevelILOperation, LLIL_TEMP,
    LowLevelILLabel,
    FlagRole,
    LowLevelILFlagCondition,
    log_error,
    CallingConvention,
    Platform)
import sys
import cffi
import os

class PPCVLE(Architecture):
    name = 'ppc_vle'
    address_size = 4
    default_int_size = 4
    max_instr_length = 4
    instr_alignment = 2
    stack_pointer = Architecture['ppc'].stack_pointer
    link_reg = Architecture['ppc'].link_reg
    endianness = Endianness.BigEndian
    regs = Architecture['ppc'].regs
    full_width_regs = Architecture['ppc'].full_width_regs
    flags = Architecture['ppc'].flags
    flag_roles = Architecture['ppc'].flag_roles
    flag_write_types = Architecture['ppc'].flag_write_types
# ['none', 'cr0_signed', 'cr1_signed', 'cr2_signed', 'cr3_signed',
# 'cr4_signed', 'cr5_signed', 'cr6_signed', 'cr7_signed', 'cr0_unsigned',
# 'cr1_unsigned', 'cr2_unsigned', 'cr3_unsigned', 'cr4_unsigned',
# 'cr5_unsigned', 'cr6_unsigned', 'cr7_unsigned', 'xer', 'xer_ca', 'xer_ov_so',
# 'mtcr0', 'mtcr1', 'mtcr2', 'mtcr3', 'mtcr4', 'mtcr5', 'mtcr6', 'mtcr7',
# 'invl0', 'invl1', 'invl2', 'invl3', 'invl4', 'invl5', 'invl6', 'invl7',
# 'invall']
    flags_written_by_flag_write_type = Architecture['ppc'].flags_written_by_flag_write_type
    flags_required_for_flag_condition = Architecture['ppc'].flags_required_for_flag_condition

    def __init__(self):
        libvle_dir = os.path.join(os.path.dirname(__file__), 'libvle')
        self.ffi = cffi.FFI()

        # with open(os.path.join(libvle_dir, 'vle.c')) as c_source:
        #     self.ffi.set_source('_libvle', c_source.read())
        self.ffi.cdef("""
            enum field_type {
                TYPE_NONE = 0,
                TYPE_REG  = 1,
                TYPE_IMM  = 2,
                TYPE_MEM  = 3,
                TYPE_JMP  = 4,
                TYPE_CR   = 5
            };

            enum op_type {
                OP_TYPE_ILL,

                OP_TYPE_ADD,
                OP_TYPE_SUB,
                OP_TYPE_MUL,
                OP_TYPE_DIV,
                OP_TYPE_SHR,
                OP_TYPE_SHL,
                OP_TYPE_ROR,

                OP_TYPE_AND,
                OP_TYPE_OR,
                OP_TYPE_XOR,
                OP_TYPE_NOR,
                OP_TYPE_NOT,

                OP_TYPE_IO,
                OP_TYPE_LOAD,
                OP_TYPE_STORE,
                OP_TYPE_MOV,

                OP_TYPE_CMP,
                OP_TYPE_JMP,
                OP_TYPE_CJMP,
                OP_TYPE_CALL,
                OP_TYPE_CCALL,
                OP_TYPE_RJMP,
                OP_TYPE_RCALL,
                OP_TYPE_RET,

                OP_TYPE_SYNC,
                OP_TYPE_SWI,
                OP_TYPE_TRAP
            };

            enum op_condition {
                COND_AL,
                COND_GE,
                COND_LE,
                COND_NE,
                COND_VC,
                COND_LT,
                COND_GT,
                COND_EQ,
                COND_VS,
                COND_NV
            };

            typedef struct {
                const uint8_t* end;
                const uint8_t* pos;
                uint16_t inc;
            } vle_handle;

            typedef struct {
                uint32_t value;
                enum field_type type;
            } vle_field_t;

            typedef struct {
                const char* name;
                vle_field_t fields[10];
                uint16_t n;
                uint16_t size;
                enum op_type op_type;
                enum op_condition cond;
            } vle_t;

            int vle_init(vle_handle* handle, const uint8_t* buffer, const uint32_t size);
            int vle_next(vle_handle* handle, vle_t* out);
            void vle_snprint(char* str, int size, uint32_t addr, vle_t* instr);
            """)
        # self.ffi.compile(verbose=True)
        self.libvle = self.ffi.dlopen(os.path.join(libvle_dir, 'libvle.so'))
        self.vle_handle = self.ffi.new('vle_handle*')
        return super(PPCVLE, self).__init__()


    def get_instruction_text(self, data, addr):
        ffi = self.ffi
        libvle = self.libvle
        vle_handle = self.vle_handle
        vle_instr = ffi.new('vle_t*')

        data_len = len(data[0:4])
        data_buf = ffi.new('char[]', data[0:4])
        return_code = libvle.vle_init(vle_handle, data_buf, data_len)
        decoding_success = libvle.vle_next(vle_handle, vle_instr);
        if not decoding_success or vle_instr.name == ffi.NULL or vle_instr.op_type == libvle.OP_TYPE_ILL:
            return None
        instr_name = ffi.string(vle_instr.name)

        FieldTypeToText = {
            libvle.TYPE_REG: lambda f, _, _2: [InstructionTextToken(InstructionTextTokenType.RegisterToken, 'r'+str(f))],
            libvle.TYPE_IMM: lambda f, _, _2: [InstructionTextToken(InstructionTextTokenType.IntegerToken, hex(f), f)],
            libvle.TYPE_MEM: lambda f, fp, _: [
                InstructionTextToken(InstructionTextTokenType.IntegerToken, hex(fp), fp),
                InstructionTextToken(InstructionTextTokenType.OperandSeparatorToken, '('),
                InstructionTextToken(InstructionTextTokenType.RegisterToken, 'r'+str(f)),
                InstructionTextToken(InstructionTextTokenType.OperandSeparatorToken, ')'),
            ],
            libvle.TYPE_JMP: lambda f, _, address: [InstructionTextToken(InstructionTextTokenType.IntegerToken, hex((f+address) & 0xffffffff), (f+address) & 0xffffffff)],
            libvle.TYPE_CR: lambda f, _, _2: [InstructionTextToken(InstructionTextTokenType.RegisterToken, 'cr'+str(f))],
        }

        tokens = [InstructionTextToken(InstructionTextTokenType.TextToken, '{:11s}'.format(instr_name))]
        loop = iter(xrange(vle_instr.n))
        for i in loop:
            f = vle_instr.fields[i]
            if f.type in FieldTypeToText:
                tokens += FieldTypeToText[f.type](f.value, vle_instr.fields[i+1].value, addr);
                tokens += [InstructionTextToken(InstructionTextTokenType.OperandSeparatorToken, ', ')]
            if f.type == libvle.TYPE_MEM:
                next(loop)

        if len(tokens) > 1:
            return tokens[:-1], vle_instr.size
        return tokens, vle_instr.size

    def get_instruction_info(self, data, addr):
        ffi = self.ffi
        libvle = self.libvle
        vle_handle = self.vle_handle
        vle_instr = ffi.new('vle_t*')

        data_len = len(data[0:4])
        data_buf = ffi.new('char[]', data[0:4])
        return_code = libvle.vle_init(vle_handle, data_buf, data_len)
        decoding_success = libvle.vle_next(vle_handle, vle_instr);
        if not decoding_success or vle_instr.name == ffi.NULL or vle_instr.op_type == libvle.OP_TYPE_ILL:
            return None

        result = InstructionInfo()
        result.length = vle_instr.size

        if vle_instr.op_type == libvle.OP_TYPE_JMP:
            result.add_branch(BranchType.UnconditionalBranch, (vle_instr.fields[0].value + addr) & 0xffffffff)
        elif vle_instr.op_type == libvle.OP_TYPE_CJMP:
            if vle_instr.fields[0].type == libvle.TYPE_JMP:
                result.add_branch(BranchType.TrueBranch, (vle_instr.fields[0].value + addr) & 0xffffffff)
                result.add_branch(BranchType.FalseBranch, result.length + addr)
            elif vle_instr.fields[0].type == libvle.TYPE_CR:
                result.add_branch(BranchType.TrueBranch, (vle_instr.fields[1].value + addr) & 0xffffffff)
                result.add_branch(BranchType.FalseBranch, result.length + addr)
            else:
                return None
        elif vle_instr.op_type == libvle.OP_TYPE_CALL:
            target = (vle_instr.fields[0].value + addr) & 0xffffffff
            if target != addr + vle_instr.size:
                result.add_branch(BranchType.CallDestination, target)
        elif vle_instr.op_type == libvle.OP_TYPE_CCALL:
            result.add_branch(BranchType.FalseBranch, result.length + addr)
            result.add_branch(BranchType.CallDestination, (vle_instr.fields[0].value + addr) & 0xffffffff)
        elif vle_instr.op_type == libvle.OP_TYPE_RJMP:
            result.add_branch(BranchType.IndirectBranch)
        elif vle_instr.op_type == libvle.OP_TYPE_RET:
            result.add_branch(BranchType.FunctionReturn)
        elif vle_instr.op_type == libvle.OP_TYPE_SWI:
            result.add_branch(BranchType.SystemCall)
        elif vle_instr.op_type == libvle.OP_TYPE_TRAP:
            result.add_branch(BranchType.FunctionReturn)

        # 0x18211100


   # 1d53a:	18 21 11 00 	e_stmvsprw 0(r1)
   # 1d53e:	18 81 11 10 	e_stmvsrrw 16(r1)
   # 1d5a4:	18 81 10 10 	e_ldmvsrrw 16(r1)
   # 1d5a8:	18 21 10 00 	e_ldmvsprw 0(r1)
        elif 'se_rfmci' == ffi.string(vle_instr.name):
            print("Failed to pick it up as a trap?")

        return result

    def cond_branch(self, il, cond, dest, false_addr):
        t = None
        if il[dest].operation == LowLevelILOperation.LLIL_CONST:
            t = il.get_label_for_address(self, il[dest].constant)
        if t is None:
            t = LowLevelILLabel()
            indirect = True
        else:
            indirect = False
        f = il.get_label_for_address(self, false_addr)
        found = f is not None
        if not found:
            f = LowLevelILLabel()
        il.append(il.if_expr(cond, t, f))
        if indirect:
            il.mark_label(t)
            il.append(il.jump(dest))
        if not found:
            il.mark_label(f)

    def get_instruction_low_level_il(self, data, addr, il):
        ffi = self.ffi
        libvle = self.libvle
        vle_handle = self.vle_handle
        vle_instr = ffi.new('vle_t*')

        data_len = len(data[0:4])
        data_buf = ffi.new('char[]', data[0:4])
        return_code = libvle.vle_init(vle_handle, data_buf, data_len)
        decoding_success = libvle.vle_next(vle_handle, vle_instr);
        # instr_name = ffi.string(instr.name)
        if not decoding_success or vle_instr.name == ffi.NULL or vle_instr.op_type == libvle.OP_TYPE_ILL:
            return None

        instr_name = ffi.string(vle_instr.name)

        should_update_flags = instr_name[-1] == '.'
        flags_to_update = 'none'
        if should_update_flags:
            flags_to_update = 'cr0_signed'
            instr_name = instr_name[:-1]

        libvle_cond_to_llil_cond = {
            libvle.COND_GE: LowLevelILFlagCondition.LLFC_SGE,
            libvle.COND_LE: LowLevelILFlagCondition.LLFC_SLE,
            libvle.COND_NE: LowLevelILFlagCondition.LLFC_NE,
            libvle.COND_VC: LowLevelILFlagCondition.LLFC_NO,
            libvle.COND_LT: LowLevelILFlagCondition.LLFC_SLT,
            libvle.COND_GT: LowLevelILFlagCondition.LLFC_SGT,
            libvle.COND_EQ: LowLevelILFlagCondition.LLFC_E,
            libvle.COND_VS: LowLevelILFlagCondition.LLFC_O
        }

        if vle_instr.op_type == libvle.OP_TYPE_SYNC:
            il.append(il.nop())
        elif vle_instr.op_type == libvle.OP_TYPE_RET:
            il.append(il.ret(il.reg(4, self.link_reg)))
        elif vle_instr.op_type == libvle.OP_TYPE_JMP:
            target = vle_instr.fields[0].value + addr
            label = il.get_label_for_address(self, target)
            if label is not None:
                expr = il.goto(label)
            else:
                expr = il.jump(il.const_pointer(4, target))
            il.append(expr)
        elif vle_instr.op_type == libvle.OP_TYPE_CALL:
            target = (vle_instr.fields[0].value + addr) & 0xffffffff
            target_expr = il.const_pointer(4, target)
            if target != addr + vle_instr.size:
                il.append(il.call(target_expr))
            else:
                il.append(il.set_reg(4, self.link_reg, target_expr))
        elif instr_name == 'se_mtctr':
            il.append(il.set_reg(4, 'ctr', il.reg(4, 'r'+str(vle_instr.fields[0].value))))
        elif instr_name == 'se_mfctr':
            il.append(il.set_reg(4, 'r'+str(vle_instr.fields[0].value), il.reg(4, 'ctr')))
        elif instr_name == 'se_mflr':
            src_reg = 'r'+str(vle_instr.fields[0].value)
            il.append(il.set_reg(4, src_reg, il.reg(4, self.link_reg)))
        elif instr_name == 'se_mtlr':
            src_reg = 'r'+str(vle_instr.fields[0].value)
            il.append(il.set_reg(4, self.link_reg, il.reg(4, src_reg)))
        elif instr_name == 'se_mtspr':
            il.append(il.set_reg(4, 'ctr', il.reg(4, 'r'+str(vle_instr.fields[0].value))))
        elif instr_name == 'se_mfspr':
            il.append(il.set_reg(4, 'r'+str(vle_instr.fields[0].value), il.reg(4, 'ctr')))
        elif instr_name == 'se_bctr':
            il.append(il.jump(il.reg(4, 'ctr')))
        elif instr_name == 'se_bctrl':
            il.append(il.call(il.reg(4, 'ctr')))
        elif instr_name == 'e_lis':
            il.append(il.set_reg(4, 'r'+str(vle_instr.fields[0].value), il.const(4, vle_instr.fields[1].value << 16)))
        elif instr_name == 'se_li':
            il.append(il.set_reg(4, 'r'+str(vle_instr.fields[0].value), il.const(4, vle_instr.fields[1].value)))
        elif instr_name == 'se_mr':
            il.append(il.set_reg(4, 'r'+str(vle_instr.fields[0].value), il.reg(4, 'r'+str(vle_instr.fields[1].value))))
        elif instr_name == 'add':
            dst_reg = 'r'+str(vle_instr.fields[0].value)
            src_reg = 'r'+str(vle_instr.fields[1].value)
            src_2 = 'r'+str(vle_instr.fields[2].value)
            il.append(il.set_reg(4, dst_reg, il.add(4, il.reg(4, src_reg), il.reg(4, src_2))))
        elif instr_name == 'se_add':
            dst_reg = 'r'+str(vle_instr.fields[0].value)
            src_reg = 'r'+str(vle_instr.fields[1].value)
            il.append(il.set_reg(4, dst_reg, il.add(4, il.reg(4, src_reg), il.reg(4, dst_reg))))
        elif instr_name == 'e_add2i':
            src_reg = 'r'+str(vle_instr.fields[0].value)
            il.append(il.set_reg(4, src_reg, il.add(4, il.reg(4, src_reg), il.const(4, vle_instr.fields[1].value), flags=flags_to_update)))
        elif instr_name == 'e_add2is':
            src_reg = 'r'+str(vle_instr.fields[0].value)
            il.append(il.set_reg(4, src_reg, il.add(4, il.reg(4, src_reg), il.const(4, vle_instr.fields[1].value << 16), flags=flags_to_update)))
        # TODO - ensure e_addi actually is handled by this
        elif instr_name in ['e_addi', 'e_add16i']:
            dst_reg = 'r'+str(vle_instr.fields[0].value)
            src_reg = 'r'+str(vle_instr.fields[1].value)
            il.append(il.set_reg(4, dst_reg, il.add(4, il.reg(4, src_reg), il.const(4, vle_instr.fields[2].value), flags=flags_to_update)))
        elif instr_name in ['se_bge', 'se_ble', 'se_bne', 'se_bns', 'se_blt', 'se_bgt', 'se_beq', 'se_bso', 'se_bc']:
            if vle_instr.fields[0].type == libvle.TYPE_JMP:
                branch_target = (vle_instr.fields[0].value + addr) & 0xffffffff
            elif vle_instr.fields[0].type == libvle.TYPE_CR:
                branch_target = (vle_instr.fields[1].value + addr) & 0xffffffff
            branch_target = il.const(4, branch_target)
            cond = il.flag_condition(libvle_cond_to_llil_cond[vle_instr.cond])
            self.cond_branch(il, cond, branch_target, addr + vle_instr.size)
        elif instr_name in ["e_bgectr", "e_blectr", "e_bnectr", "e_bnsctr", "e_bltctr", "e_bgtctr", "e_beqctr", "e_bsoctr", "e_bcctr"]:
            branch_target = il.reg(4, 'ctr')
            cond = il.flag_condition(libvle_cond_to_llil_cond[vle_instr.cond])
            self.cond_branch(il, cond, branch_target, addr + vle_instr.size)
        elif instr_name == 'e_crxor':
            dst_reg = 'r'+str(vle_instr.fields[0].value)
            src_1 = 'r'+str(vle_instr.fields[1].value)
            src_2 = 'r'+str(vle_instr.fields[2].value)
            il.append(il.set_reg(4, dst_reg, il.xor_expr(4, il.reg(4, src_1), il.reg(4, src_2), flags='cr0_unsigned')))
        elif instr_name == 'subf':
            dst_reg = 'r'+str(vle_instr.fields[0].value)
            src_reg = 'r'+str(vle_instr.fields[1].value)
            src_2 = 'r'+str(vle_instr.fields[2].value)
            il.append(il.set_reg(4, dst_reg, il.sub(4, il.reg(4, src_2), il.reg(4, src_reg))))
        elif instr_name == 'se_subf':
            dst_reg = 'r'+str(vle_instr.fields[0].value)
            src_reg = 'r'+str(vle_instr.fields[1].value)
            il.append(il.set_reg(4, dst_reg, il.sub(4, il.reg(4, src_reg), il.reg(4, dst_reg))))
        elif instr_name == 'se_sub':
            dst_reg = 'r'+str(vle_instr.fields[0].value)
            src_reg = 'r'+str(vle_instr.fields[1].value)
            il.append(il.set_reg(4, dst_reg, il.sub(4, il.reg(4, dst_reg), il.reg(4, src_reg))))
        elif instr_name == 'se_bgeni':
            dst_reg = 'r'+str(vle_instr.fields[0].value)
            constant = 0x80000000 >> vle_instr.fields[1].value
            il.append(il.set_reg(4, dst_reg, il.const(4, constant)))
        elif instr_name in ['e_lwz', 'se_lwz']:
            dst_reg = 'r'+str(vle_instr.fields[0].value)
            offset = vle_instr.fields[2].value
            base_reg = 'r'+str(vle_instr.fields[1].value)
            il.append(il.set_reg(4, dst_reg, il.load(4, il.add(4, il.reg(4, base_reg), il.const(4, offset)))))
        elif instr_name in ['e_stwu', 'se_stw', 'e_stw']:
            src_reg = 'r'+str(vle_instr.fields[0].value)
            offset = vle_instr.fields[2].value
            base_reg = 'r'+str(vle_instr.fields[1].value)
            il.append(il.store(4, il.add(4, il.reg(4, base_reg), il.const(4, offset)),
                               il.reg(4, src_reg)))
        elif instr_name == 'se_stb':
            src_reg = 'r'+str(vle_instr.fields[0].value)
            offset = vle_instr.fields[2].value
            base_reg = 'r'+str(vle_instr.fields[1].value)
            il.append(il.store(1, il.add(4, il.reg(4, base_reg), il.const(4, offset)),
                               il.reg(4, src_reg)))
        elif instr_name == 'e_stmw':
            offset = vle_instr.fields[2].value
            base_reg = 'r'+str(vle_instr.fields[1].value)
            for i in range(vle_instr.fields[0].value, 32):
                il.append(il.store(4, il.add(4, il.reg(4, base_reg), il.const(4, offset)),
                               il.reg(4, 'r'+str(i))))
                offset = offset + 4
        elif instr_name == 'e_lmw':
            offset = vle_instr.fields[2].value
            base_reg = 'r'+str(vle_instr.fields[1].value)
            for i in range(vle_instr.fields[0].value, 32):
                il.append(il.set_reg(4, 'r'+str(i), il.load(4, il.add(4, il.reg(4, base_reg), il.const(4, offset)))))
                # il.append(il.store(4, il.add(4, il.reg(4, base_reg), il.const(4, offset)),
                #                il.reg(4, 'r'+str(i))))
                offset = offset + 4
        elif instr_name == 'e_rlwinm':
            # print("Generating e_rlwinm")
            dst_reg = 'r'+str(vle_instr.fields[0].value)
            src_reg = 'r'+str(vle_instr.fields[1].value)
            rotate_amt = vle_instr.fields[2].value
            mask_start = vle_instr.fields[3].value
            mask_end = vle_instr.fields[4].value
            # TODO - a compiler bug can lead to seemingly nonsensical mask
            # generation limits, which are not defined well in the spec. I need
            # to check out what this does on real hardware.
            # print("address: {addr_hex} dst_reg: {dst_reg}, src {src_reg}, rotate {rotate_amt}, mask_start {mask_start}, mask_end {mask_end}".format(addr_hex=hex(addr), **locals()))
            if mask_start > mask_end:
                il.append(il.unimplemented())
            else:
                mask = ((1 << (mask_end - mask_start + 1)) - 1) << (31 - mask_end)
                rotated = il.rotate_left(4, il.reg(4, src_reg), il.const(4, rotate_amt))
                masked = il.and_expr(4, rotated, il.const(4, mask))
                il.append(il.set_reg(4, dst_reg, masked))
        elif instr_name == 'se_cmpl':
            reg1 = 'r'+str(vle_instr.fields[0].value)
            reg2 = 'r'+str(vle_instr.fields[1].value)
            # TODO: Check the order of operands here to make sure I've not got them backwards
            il.append(il.sub(4, il.reg(4, reg1), il.reg(4, reg2), flags='cr0_unsigned'))
        elif instr_name == 'se_cmpi':
            reg1 = 'r'+str(vle_instr.fields[0].value)
            immed = vle_instr.fields[1].value
            il.append(il.sub(4, il.reg(4, reg1), il.const(4, immed), flags='cr0_signed'))
        elif instr_name == 'neg':
            dst_reg = 'r'+str(vle_instr.fields[0].value)
            src_reg = 'r'+str(vle_instr.fields[1].value)
            il.append(il.set_reg(4, dst_reg, il.neg_expr(4, il.reg(4, src_reg))))
        elif instr_name == 'se_or':
            dst_reg = 'r'+str(vle_instr.fields[0].value)
            src_reg = 'r'+str(vle_instr.fields[1].value)
            il.append(il.set_reg(4, dst_reg, il.or_expr(4, il.reg(4,dst_reg), il.reg(4, src_reg))))
        elif instr_name == 'srw':
            dst_reg = 'r'+str(vle_instr.fields[0].value)
            shift_amt = 'r'+str(vle_instr.fields[1].value)
            src_reg = 'r'+str(vle_instr.fields[2].value)
            il.append(il.set_reg(4, dst_reg, il.logical_shift_right(4, il.reg(4, src_reg), il.reg(4, shift_amt))))
        elif instr_name == 'slw':
            dst_reg = 'r'+str(vle_instr.fields[0].value)
            shift_amt = 'r'+str(vle_instr.fields[1].value)
            src_reg = 'r'+str(vle_instr.fields[2].value)
            il.append(il.set_reg(4, dst_reg, il.shift_left(4, il.reg(4, src_reg), il.reg(4, shift_amt))))
        elif instr_name == 'se_srwi':
            il.append(il.unimplemented())
        elif instr_name == 'e_or2i':
            il.append(il.unimplemented())
        elif instr_name == 'srawi.': # this also needs implementing in libvle
            il.append(il.unimplemented())

        # 2718:	7c 00 01 46 	wrteei  0
        else:
            il.append(il.unimplemented())
        # il.append(il.unimplemented())

        return vle_instr.size

PPCVLE.register()

class VleCallingConvention(CallingConvention):
    name = 'vle-abi'
    caller_saved_regs = ['r0', 'r2', 'r3', 'r4', 'r5', 'r6', 'r7', 'r8', 'r9', 'r10', 'r11', 'r12']
    int_arg_regs = ['r3', 'r4', 'r5', 'r6', 'r7', 'r8', 'r9', 'r10']
    int_return_reg = 'r3'

Architecture['ppc_vle'].register_calling_convention(VleCallingConvention(Architecture['ppc_vle'], 'vle-abi'))

class VlePlatform(Platform):
    name = 'vle'

platform = VlePlatform(Architecture['ppc_vle'])
platform.default_calling_convention = Architecture['ppc_vle'].calling_conventions['vle-abi']
platform.register('vle')
