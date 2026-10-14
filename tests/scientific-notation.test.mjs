import test from 'node:test';
import assert from 'node:assert/strict';
import { scientificNotation as fmt } from '../src/scientificNotation.ts';

test('equivalent scientific representations use one notation',()=>{
 for(const source of ['1.2e-3','1.2E-03','1.2×10^-3','1.2 x 10^{-3}','1.2 × 10⁻³'])assert.equal(fmt(source),'1.2 × 10⁻³');
 assert.equal(fmt('>10^12 cycles; 4×10^11 cycles; <10^-3 A/cm^2'),'>10¹² cycles; 4 × 10¹¹ cycles; <10⁻³ A/cm²');
});
test('reported precision, inequalities and ranges survive',()=>{
 assert.equal(fmt('≥1.20e-9 A/µm'),'≥1.20 × 10⁻⁹ A/μm');
 assert.equal(fmt('1.20 × 10^-9–2.50 × 10^-8'),'1.20 × 10⁻⁹–2.50 × 10⁻⁸');
 assert.equal(fmt('0.000100 A; 15000 s'),'1.00 × 10⁻⁴ A; 1.5 × 10⁴ s');
 assert.equal(fmt('-46%; 0.9 V; 90 °C; 1.8–2.6×'),'−46%; 0.9 V; 90 °C; 1.8–2.6×');
 assert.equal(fmt('65k reads; 0.19x; >=1e12 cycles; 65 K'),'6.5 × 10⁴ reads; 0.19×; ≥10¹² cycles; 65 K');
});
test('already formatted text is stable and ambiguous exponents are not guessed',()=>{
 const values=['>10¹² cycles','6.6 × 10⁻⁹ Ω·cm²','10 2 cycles','cm2','HfO2','Table 2.1, 2026','2.65 eV','10,000','1.2e-3'];
 for(const value of values)assert.equal(fmt(fmt(value)),fmt(value));
 assert.equal(fmt('10 2 cycles; 2.65 eV; HfO2'),'10 2 cycles; 2.65 eV; HfO2');
 assert.equal(fmt('test <script>alert(1)</script>'),'test <script>alert(1)</script>');
});
