using System;

namespace CapacitorLibrary
{
    /// <summary>
    /// Расчёт ёмкости плоского конденсатора
    /// (задание 1 лабораторной работы №4)
    /// </summary>
    public class Capacitor
    {
        /// <summary>
        /// Ёмкость C = eps * S / (4 * pi * d)
        /// </summary>
        /// <param name="eps">проницаемость, от 1</param>
        /// <param name="s">площадь пластины, больше 0</param>
        /// <param name="d">расстояние, больше 0</param>
        public static double Capacity(double eps, double s,
            double d)
        {
            if (s <= 0)
            {
                throw new ArgumentException(
                    "Площадь S должна быть больше нуля");
            }
            if (d <= 0)
            {
                throw new ArgumentException(
                    "Расстояние d должно быть больше нуля");
            }
            if (eps < 1)
            {
                throw new ArgumentException(
                    "Проницаемость eps должна быть не меньше 1");
            }
            return eps * s / (4.0 * Math.PI * d);
        }
    }
}
